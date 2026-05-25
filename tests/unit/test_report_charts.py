"""Unit tests for the inline SVG timing-chart renderer."""

from __future__ import annotations

from diagforge.ingestion.models import TraceEvent
from diagforge.report.charts import render_signal_chart
from diagforge.report.models import (
    DiagnosticResult,
    DtcAnalysis,
    DtcInfo,
    Hypothesis,
    PatternFeatures,
    TransitionAnomaly,
)


def _ev(ts_us: int, signal: str, value: float) -> TraceEvent:
    return TraceEvent(
        timestamp_us=ts_us,
        channel=0,
        frame_id=0x100,
        dlc=8,
        data=b"\x00" * 8,
        decoded_signals={signal: value},
    )


def _analysis_with_anomalies(*evidence_us_lists: list[int]) -> DtcAnalysis:
    return DtcAnalysis(
        dtc=DtcInfo(
            code="P0300",
            standard="obd2",
            occurrence_count=1,
            first_seen_us=400_000,
            last_seen_us=700_000,
        ),
        pattern_features=PatternFeatures(
            window_us=500_000,
            transition_anomalies=[
                TransitionAnomaly(
                    signal_name="x",
                    anomaly_type="signal_dropout",
                    description="x dropped",
                    evidence_us=ev_list,
                )
                for ev_list in evidence_us_lists
            ],
            notable_findings=["finding"],
        ),
        diagnostic_result=DiagnosticResult(
            hypotheses=[
                Hypothesis(
                    rank=1,
                    description="x",
                    confidence="low",
                    evidence=["e"],
                    reasoning="r",
                )
            ],
            model="m",
            model_version="v",
            prompt_template_version="diag-v1",
        ),
        mitigation_matches=[],
    )


def test_chart_returns_svg_with_signal_data() -> None:
    events = [_ev(i * 10_000, "rpm", 800.0 + (i % 5)) for i in range(60)]
    svg = render_signal_chart(events, _analysis_with_anomalies([100_000], [200_000]))
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    # Plausibly large — a real chart with axes/legend/data is many KB.
    assert len(svg) > 4000
    # Anomaly markers are drawn in the warning colour; presence proves the
    # axvline branch fired even though matplotlib renders them as <path>.
    assert "#d96528" in svg.lower() or "d96528" in svg


def test_chart_empty_when_no_decoded_signals() -> None:
    events = [
        TraceEvent(timestamp_us=i * 10_000, channel=0, frame_id=0x100, dlc=0, data=b"")
        for i in range(10)
    ]
    assert render_signal_chart(events, _analysis_with_anomalies([])) == ""


def test_chart_multi_signal() -> None:
    events: list[TraceEvent] = []
    for i in range(40):
        events.append(_ev(i * 10_000, "rpm", 800.0))
        events.append(
            TraceEvent(
                timestamp_us=i * 10_000,
                channel=0,
                frame_id=0x200,
                dlc=8,
                data=b"\x00" * 8,
                decoded_signals={"v": 12.5 + (i % 3) * 0.01},
            )
        )
    svg = render_signal_chart(events, _analysis_with_anomalies([50_000]))
    # Multi-signal chart is bigger than single-signal; legend includes two paths.
    assert svg.startswith("<svg")
    assert len(svg) > 6000
    # Both line colors from the palette should appear (matplotlib paths use
    # explicit `stroke: #...` styles).
    assert "#1f77b4" in svg.lower()
    assert "#2ca02c" in svg.lower()
