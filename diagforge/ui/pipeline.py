"""Pipeline helper for the UI — runs ingestion → analysis → LLM → emit.

Pulled out of `app.py` so it can be exercised by unit tests without
importing Streamlit (Streamlit refuses to be imported outside a `streamlit
run` session in some environments). The UI module imports `run_pipeline`
and streams progress via the `progress` callback.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from diagforge.analyzer.cross_dtc import detect_findings as detect_cross_dtc_findings
from diagforge.analyzer.timing import build_pattern_features, slice_events_for_dtc
from diagforge.diagnostic.agent import AnthropicClient, DiagnosticAgent, RealAnthropicClient
from diagforge.ingestion.dtc_json import DtcJsonParser
from diagforge.ingestion.registry import format_for, parser_for
from diagforge.ingestion.signal_decode import SignalDecoder
from diagforge.mitigation.library import MitigationLibrary
from diagforge.mitigation.recommender import MitigationRecommender
from diagforge.report.emitter import ReportEmitter, sha256_path
from diagforge.report.models import (
    DbcFileInfo,
    DtcAnalysis,
    DtcFileInfo,
    DtcInfo,
    InputInfo,
    Report,
    TraceFileInfo,
)

if TYPE_CHECKING:
    pass

ProgressFn = Callable[[str], None]


class PipelineResult:
    """Everything the UI needs to render after a run completes."""

    def __init__(
        self,
        report: Report,
        report_json_bytes: bytes,
        report_html_bytes: bytes,
        manifest_json_bytes: bytes,
        charts: list[str],
        bundle_zip_bytes: bytes,
    ) -> None:
        self.report = report
        self.report_json_bytes = report_json_bytes
        self.report_html_bytes = report_html_bytes
        self.manifest_json_bytes = manifest_json_bytes
        self.charts = charts
        self.bundle_zip_bytes = bundle_zip_bytes


def _progress_noop(_msg: str) -> None:
    pass


def run_pipeline(
    trace_path: Path,
    dtcs_path: Path,
    dbc_path: Path | None,
    out_dir: Path,
    model: str = "claude-opus-4-7",
    window_ms: int = 500,
    client: AnthropicClient | None = None,
    progress: ProgressFn | None = None,
) -> PipelineResult:
    """End-to-end pipeline — same as the CLI's analyze command, callable as a library.

    `client` overrides the default `RealAnthropicClient` (used by tests and
    can be used by the UI to inject any compatible client). `progress` is
    invoked with short human-readable status strings before each major step.
    """
    progress = progress or _progress_noop

    progress(f"Ingesting trace {trace_path.name}")
    events = parser_for(trace_path).parse(trace_path)

    progress(f"Decoding {len(events)} frames")
    decoder = SignalDecoder(dbc_path)
    events = decoder.decode(events)

    progress(f"Loading DTC snapshot {dtcs_path.name}")
    dtcs = DtcJsonParser().parse(dtcs_path)

    library = MitigationLibrary.from_packaged_data()
    pattern_ids = library.list_pattern_ids()

    agent_client = client or RealAnthropicClient()
    agent = DiagnosticAgent(agent_client, model=model)
    recommender = MitigationRecommender(library)

    input_info = InputInfo(
        trace_file=TraceFileInfo(
            path=str(trace_path),
            format=format_for(trace_path),  # type: ignore[arg-type]
            sha256=sha256_path(trace_path),
            event_count=len(events),
            duration_us=(events[-1].timestamp_us - events[0].timestamp_us) if events else 0,
        ),
        dtc_file=DtcFileInfo(
            path=str(dtcs_path),
            sha256=sha256_path(dtcs_path),
            dtc_count=len(dtcs),
        ),
        dbc_file=(
            DbcFileInfo(path=str(dbc_path), sha256=sha256_path(dbc_path))
            if dbc_path is not None
            else None
        ),
    )
    emitter = ReportEmitter(out_dir, input_info)
    cross = detect_cross_dtc_findings(dtcs)
    if cross:
        progress(f"Detected {len(cross)} cross-DTC relationship(s)")
        emitter.set_cross_dtc_findings(cross)
    window_us = max(50_000, window_ms * 1_000)

    for i, dtc in enumerate(dtcs, 1):
        progress(f"Analyzing DTC {i}/{len(dtcs)}: {dtc.dtc_code}")
        slice_ = slice_events_for_dtc(events, dtc, window_us)
        features = build_pattern_features(events, dtc, window_us=window_us)
        progress(f"  → asking Claude for hypotheses on {dtc.dtc_code}")
        result = agent.propose(dtc, features, pattern_ids)
        matches = recommender.match(result, features)
        emitter.add_analysis(
            DtcAnalysis(
                dtc=DtcInfo(
                    code=dtc.dtc_code,
                    standard=dtc.standard,
                    status_byte=dtc.status_byte,
                    occurrence_count=dtc.occurrence_count,
                    first_seen_us=dtc.timestamp_first_us,
                    last_seen_us=dtc.timestamp_latest_us,
                    description=dtc.description,
                ),
                pattern_features=features,
                diagnostic_result=result,
                mitigation_matches=matches,
            ),
            events_slice=slice_,
        )

    progress("Writing report bundle")
    report_json_path = emitter.finalize()

    # Read back the on-disk bytes so the UI can serve them as downloads.
    report_json_bytes = report_json_path.read_bytes()
    report_html_bytes = (out_dir / "report.html").read_bytes()
    manifest_json_bytes = (out_dir / "manifest.json").read_bytes()

    # Re-load the report to expose its parsed form to the UI.
    report = Report.model_validate_json(report_json_bytes)

    # Re-render charts here too so the UI can show them in-page without
    # having to parse them out of the HTML.
    from diagforge.report.charts import render_signal_chart  # local import: matplotlib

    charts: list[str] = []
    for analysis in report.analyses:
        slice_ = slice_events_for_dtc(events, _dtc_snapshot_from(analysis), window_us)
        charts.append(render_signal_chart(slice_, analysis))

    bundle_zip_bytes = _bundle_zip(report_json_bytes, report_html_bytes, manifest_json_bytes)

    progress(f"Done — {len(dtcs)} DTC(s) analysed")
    return PipelineResult(
        report=report,
        report_json_bytes=report_json_bytes,
        report_html_bytes=report_html_bytes,
        manifest_json_bytes=manifest_json_bytes,
        charts=charts,
        bundle_zip_bytes=bundle_zip_bytes,
    )


def _dtc_snapshot_from(analysis: DtcAnalysis):  # type: ignore[no-untyped-def]
    """Reconstruct enough of a DTCSnapshot for slice_events_for_dtc."""
    from diagforge.ingestion.models import DTCSnapshot

    return DTCSnapshot(
        dtc_code=analysis.dtc.code,
        standard=analysis.dtc.standard,
        timestamp_first_us=analysis.dtc.first_seen_us or 0,
        timestamp_latest_us=analysis.dtc.last_seen_us or (analysis.dtc.first_seen_us or 0),
        occurrence_count=analysis.dtc.occurrence_count or 1,
        description=analysis.dtc.description,
    )


def _bundle_zip(report_json: bytes, report_html: bytes, manifest_json: bytes) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", report_json)
        zf.writestr("report.html", report_html)
        zf.writestr("manifest.json", manifest_json)
    return buf.getvalue()
