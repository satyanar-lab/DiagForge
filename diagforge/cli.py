"""DiagForge command-line entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from diagforge import __version__
from diagforge._logging import configure_logging, get_logger
from diagforge.analyzer.cross_dtc import detect_findings as detect_cross_dtc_findings
from diagforge.analyzer.timing import build_pattern_features, slice_events_for_dtc
from diagforge.diagnostic.agent import DiagnosticAgent, RealAnthropicClient
from diagforge.ingestion.dtc_json import DtcJsonParser
from diagforge.ingestion.registry import format_for, parser_for, supported_extensions
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
    TraceFileInfo,
)

if TYPE_CHECKING:
    from diagforge.diagnostic.agent import AnthropicClient

_log = get_logger(__name__)


@click.group()
@click.version_option(__version__, prog_name="diagforge")
def main() -> None:
    """DiagForge — vehicle DTC root-cause co-pilot."""


@main.command()
@click.argument("trace_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--dtcs",
    "dtcs_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the DTC snapshot JSON file.",
)
@click.option(
    "--output",
    "-o",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Directory to write report.json, report.html, manifest.json.",
)
@click.option(
    "--dbc",
    "dbc_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional DBC for signal decoding; without it, an auto-decoder is used.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
@click.option(
    "--model",
    default="claude-opus-4-7",
    show_default=True,
    help="Claude model to use for diagnostic hypotheses.",
)
@click.option(
    "--window-ms",
    type=int,
    default=500,
    show_default=True,
    help=(
        "Analysis window (milliseconds) around each DTC's first/last occurrence. "
        "Increase for slow-oscillating signals (catalyst monitors, ~1-3s)."
    ),
)
def analyze(
    trace_path: Path,
    dtcs_path: Path,
    out_dir: Path,
    dbc_path: Path | None,
    verbose: bool,
    model: str,
    window_ms: int,
) -> None:
    """Analyze a diagnostic trace and emit a DiagForge evidence bundle."""
    configure_logging(verbose=verbose)

    if trace_path.suffix.lower() not in supported_extensions():
        accepted = ", ".join(supported_extensions())
        click.echo(
            f"unsupported trace extension {trace_path.suffix!r}; accepted: {accepted}",
            err=True,
        )
        sys.exit(2)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo("ANTHROPIC_API_KEY is not set; aborting.", err=True)
        sys.exit(2)

    _log.info("ingesting trace %s", trace_path)
    events = parser_for(trace_path).parse(trace_path)
    decoder = SignalDecoder(dbc_path)
    events = decoder.decode(events)

    _log.info("ingesting DTCs %s", dtcs_path)
    dtcs = DtcJsonParser().parse(dtcs_path)

    library = MitigationLibrary.from_packaged_data()
    pattern_ids = library.list_pattern_ids()

    client: AnthropicClient = RealAnthropicClient()
    agent = DiagnosticAgent(client, model=model)
    recommender = MitigationRecommender(library)

    trace_fmt = format_for(trace_path)
    input_info = InputInfo(
        trace_file=TraceFileInfo(
            path=str(trace_path),
            format=trace_fmt,  # type: ignore[arg-type]
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
    cross_dtc = detect_cross_dtc_findings(dtcs)
    if cross_dtc:
        _log.info("found %d cross-DTC relationship(s)", len(cross_dtc))
        emitter.set_cross_dtc_findings(cross_dtc)

    window_us = max(50_000, window_ms * 1_000)
    for dtc in dtcs:
        _log.info("analyzing DTC %s (window %dms)", dtc.dtc_code, window_us // 1000)
        slice_ = slice_events_for_dtc(events, dtc, window_us)
        features = build_pattern_features(events, dtc, window_us=window_us)
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

    report_json = emitter.finalize()
    click.echo(f"wrote {report_json}")
    click.echo(f"wrote {report_json.with_name('report.html')}")


if __name__ == "__main__":
    main()
