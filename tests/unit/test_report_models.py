"""Round-trip and schema-conformance tests for the report models."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from diagforge.report.models import (
    DiagnosticResult,
    DtcAnalysis,
    DtcFileInfo,
    DtcInfo,
    Hypothesis,
    InputInfo,
    IntervalStats,
    MitigationMatch,
    PatternFeatures,
    Report,
    SignalSummary,
    ToolInfo,
    TraceFileInfo,
    TransitionAnomaly,
)

VALID_UUID7 = "01890c1d-e000-7000-8000-aaaabbbbcccc"
VALID_SHA = "a" * 64


def _make_minimal_report() -> Report:
    return Report(
        report_id=VALID_UUID7,
        created_at=dt.datetime(2026, 5, 24, 12, 0, tzinfo=dt.UTC),
        tool=ToolInfo(version="0.0.1"),
        input=InputInfo(
            trace_file=TraceFileInfo(path="trace.asc", format="asc", sha256=VALID_SHA),
            dtc_file=DtcFileInfo(path="dtcs.json", sha256=VALID_SHA),
        ),
        analyses=[
            DtcAnalysis(
                dtc=DtcInfo(code="P0300", standard="obd2", occurrence_count=1),
                pattern_features=PatternFeatures(
                    window_us=500_000,
                    signal_summaries=[
                        SignalSummary(
                            signal_name="engine_rpm",
                            transition_rate_hz=10.0,
                            interval_stats=IntervalStats(mean_us=10_000, median_us=10_000),
                        )
                    ],
                    transition_anomalies=[
                        TransitionAnomaly(
                            signal_name="engine_rpm",
                            anomaly_type="signal_dropout",
                            description="4 dropouts in 200ms",
                            evidence_us=[10_000, 50_000],
                        )
                    ],
                    notable_findings=["engine_rpm dropped 4 times in 200ms"],
                ),
                diagnostic_result=DiagnosticResult(
                    hypotheses=[
                        Hypothesis(
                            rank=1,
                            description="Intermittent dropout",
                            confidence="medium",
                            evidence=["engine_rpm dropped 4 times in 200ms"],
                            suggested_pattern_id="dematuration_timer",
                            reasoning="Brief drops near idle threshold",
                        )
                    ],
                    model="claude-sonnet-4-6",
                    model_version="2026-01-01",
                    prompt_template_version="diag-v1",
                ),
                mitigation_matches=[
                    MitigationMatch(
                        pattern_id="dematuration_timer",
                        pattern_name="Dematuration / fault-clear timer",
                        verification_steps=["Hold signal at threshold"],
                    )
                ],
            )
        ],
    )


def test_round_trip_through_json() -> None:
    report = _make_minimal_report()
    blob = report.model_dump_json()
    restored = Report.model_validate_json(blob)
    assert restored == report


def test_bad_report_id_rejected() -> None:
    with pytest.raises(ValidationError, match="UUIDv7"):
        Report(
            report_id="not-a-uuid",
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            tool=ToolInfo(version="0.0.1"),
            input=InputInfo(
                trace_file=TraceFileInfo(path="t.asc", format="asc", sha256=VALID_SHA),
                dtc_file=DtcFileInfo(path="d.json", sha256=VALID_SHA),
            ),
        )


def test_bad_sha256_rejected() -> None:
    with pytest.raises(ValidationError, match="sha256"):
        TraceFileInfo(path="t.asc", format="asc", sha256="short")


def test_correlation_coefficient_bounds() -> None:
    from diagforge.report.models import CrossSignalCorrelation

    CrossSignalCorrelation(signal_a="a", signal_b="b", lag_us=0, correlation_coefficient=1.0)
    CrossSignalCorrelation(signal_a="a", signal_b="b", lag_us=0, correlation_coefficient=-1.0)
    with pytest.raises(ValidationError):
        CrossSignalCorrelation(signal_a="a", signal_b="b", lag_us=0, correlation_coefficient=1.5)


def test_dumped_json_matches_schema_required_fields() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "claude" / "report-schema.json"
    schema = json.loads(schema_path.read_text())
    required = set(schema["required"])
    dumped = _make_minimal_report().model_dump(mode="json")
    assert required.issubset(dumped.keys())
    # spot-check nested required fields
    a0 = dumped["analyses"][0]
    assert {"dtc", "pattern_features", "diagnostic_result", "mitigation_matches"}.issubset(a0)


def test_unknown_field_rejected_at_top_level() -> None:
    with pytest.raises(ValidationError):
        Report.model_validate(
            {
                "report_id": VALID_UUID7,
                "schema_version": "1.0.0",
                "created_at": "2026-01-01T00:00:00Z",
                "tool": {"name": "diagforge", "version": "0.0.1"},
                "input": {
                    "trace_file": {"path": "t.asc", "format": "asc", "sha256": VALID_SHA},
                    "dtc_file": {"path": "d.json", "sha256": VALID_SHA},
                },
                "analyses": [],
                "extra_field": "nope",
            }
        )
