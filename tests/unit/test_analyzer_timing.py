"""Unit tests for the deterministic pattern analyzer."""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from diagforge.analyzer.timing import (
    build_pattern_features,
    compute_signal_summaries,
    detect_power_cycle_bursts,
    detect_transition_anomalies,
    detect_value_anomalies,
)
from diagforge.ingestion.models import DTCSnapshot, TraceEvent


def _ev(ts_us: int, signal: str, value: float, frame_id: int = 0x100) -> TraceEvent:
    return TraceEvent(
        timestamp_us=ts_us,
        channel=0,
        frame_id=frame_id,
        dlc=8,
        data=b"\x00" * 8,
        decoded_signals={signal: value},
    )


# ---------- compute_signal_summaries ----------


class TestSignalSummaries:
    def test_constant_signal_zero_transition_rate(self) -> None:
        events = [_ev(i * 10_000, "rpm", 800.0) for i in range(20)]
        summaries = compute_signal_summaries(events, ["rpm"], window_us=200_000)
        assert len(summaries) == 1
        assert summaries[0].transition_rate_hz == 0.0
        assert summaries[0].interval_stats.median_us == 10_000.0

    def test_alternating_signal_high_transition_rate(self) -> None:
        events = [_ev(i * 10_000, "sw", float(i % 2)) for i in range(20)]
        summaries = compute_signal_summaries(events, ["sw"], window_us=200_000)
        # 19 transitions over 200ms = 95 Hz
        assert summaries[0].transition_rate_hz == pytest.approx(95.0, rel=0.05)

    def test_missing_signal_omitted(self) -> None:
        events = [_ev(0, "rpm", 800.0)]
        assert compute_signal_summaries(events, ["unknown"], window_us=1_000_000) == []


# ---------- detect_transition_anomalies ----------


class TestTransitionAnomalies:
    def test_no_anomaly_when_below_threshold(self) -> None:
        events = [_ev(i * 100_000, "sw", float(i % 2)) for i in range(4)]
        # 3 changes in a 50ms window? No — spacing is 100ms each.
        assert detect_transition_anomalies(events, "sw") == []

    def test_burst_flagged(self) -> None:
        events = [_ev(i * 1_000, "sw", float(i % 2)) for i in range(20)]
        # 19 changes within 20ms easily exceeds 5/50ms.
        anomalies = detect_transition_anomalies(events, "sw")
        assert len(anomalies) >= 1
        assert anomalies[0].anomaly_type == "debounce_candidate"
        assert "sw" in anomalies[0].description


# ---------- detect_value_anomalies ----------


class TestValueAnomalies:
    def test_clean_signal_no_anomalies(self) -> None:
        rng = random.Random(0)
        events = [_ev(i * 10_000, "rpm", 800.0 + rng.uniform(-5, 5)) for i in range(50)]
        assert detect_value_anomalies(events, "rpm") == []

    def test_four_dropouts_detected_on_p0300_shape(self) -> None:
        """Replicates the P0300 scenario: idle frames interleaved with 4 brief dropouts.

        Each dropout spans 3 consecutive frames at ~10ms framing (~30ms wide),
        separated by idle frames so the detector resolves them as 4 distinct
        runs rather than one merged cluster.
        """
        rng = random.Random(42)
        events: list[TraceEvent] = []
        # 50 idle frames at 800 RPM ±10
        for i in range(50):
            events.append(_ev(i * 10_000, "rpm", 800.0 + rng.uniform(-10, 10)))
        # 4 dropouts, each 3 frames wide, separated by 2 idle frames
        dropout_starts_us = [510_000, 580_000, 650_000, 720_000]
        dropout_depths = [50.0, 90.0, 70.0, 130.0]
        next_idle = max(dropout_starts_us) + 3 * 10_000
        for start, depth in zip(dropout_starts_us, dropout_depths, strict=True):
            for k in range(3):
                events.append(_ev(start + k * 10_000, "rpm", depth + rng.uniform(-5, 5)))
            # one idle frame immediately after the dropout completes
            events.append(_ev(start + 3 * 10_000, "rpm", 800.0 + rng.uniform(-10, 10)))
        # post-dropout idle
        for i in range(10):
            events.append(_ev(next_idle + 50_000 + i * 10_000, "rpm", 800.0 + rng.uniform(-10, 10)))
        events.sort(key=lambda e: e.timestamp_us)

        anomalies = detect_value_anomalies(events, "rpm")
        assert len(anomalies) == 4, [a.description for a in anomalies]
        for a in anomalies:
            assert a.anomaly_type == "signal_dropout"
            assert "rpm" in a.description

    def test_flat_signal_does_not_divide_by_zero(self) -> None:
        events = [_ev(i * 10_000, "x", 1.0) for i in range(10)]
        # All values identical → MAD == 0 → no anomalies and no crash.
        assert detect_value_anomalies(events, "x") == []

    def test_spike_classified_as_value_spike(self) -> None:
        rng = random.Random(1)
        events = [_ev(i * 10_000, "v", 12.0 + rng.uniform(-0.05, 0.05)) for i in range(50)]
        events.append(_ev(500_000, "v", 18.0))
        events.append(_ev(510_000, "v", 18.0))
        events.append(_ev(520_000, "v", 18.0))
        events.sort(key=lambda e: e.timestamp_us)
        anomalies = detect_value_anomalies(events, "v")
        assert any(a.anomaly_type == "value_spike" for a in anomalies)


# ---------- detect_power_cycle_bursts ----------


class TestPowerCycleBursts:
    def test_two_collapses_within_window(self) -> None:
        events = [_ev(i * 10_000, "vbat", 12.5) for i in range(20)]
        events.append(_ev(220_000, "vbat", 2.0))
        events.append(_ev(330_000, "vbat", 2.0))
        events.sort(key=lambda e: e.timestamp_us)
        bursts = detect_power_cycle_bursts(events, "vbat", window_us=200_000)
        assert len(bursts) == 1
        assert bursts[0].anomaly_type == "power_cycle_burst"

    def test_isolated_collapse_not_reported(self) -> None:
        events = [_ev(i * 10_000, "vbat", 12.5) for i in range(20)]
        events.append(_ev(220_000, "vbat", 2.0))
        assert detect_power_cycle_bursts(events, "vbat") == []


# ---------- build_pattern_features ----------


class TestBuildPatternFeatures:
    def test_quiet_signal_produces_a_no_anomaly_finding(self) -> None:
        events = [_ev(i * 10_000, "rpm", 800.0) for i in range(100)]
        dtc = DTCSnapshot(
            dtc_code="P0300",
            standard="obd2",
            timestamp_first_us=400_000,
            timestamp_latest_us=400_000,
            occurrence_count=1,
        )
        feats = build_pattern_features(events, dtc)
        assert feats.window_us == 500_000
        assert any("steady-state" in f or "No value" in f for f in feats.notable_findings)

    def test_p0300_shaped_input_produces_dropout_finding(self) -> None:
        rng = random.Random(42)
        events: list[TraceEvent] = []
        for i in range(50):
            events.append(_ev(i * 10_000, "rpm", 800.0 + rng.uniform(-10, 10)))
        for start_us, depth in [
            (510_000, 50.0),
            (580_000, 90.0),
            (650_000, 70.0),
            (720_000, 130.0),
        ]:
            for k in range(3):
                events.append(_ev(start_us + k * 10_000, "rpm", depth + rng.uniform(-5, 5)))
            events.append(_ev(start_us + 3 * 10_000, "rpm", 800.0 + rng.uniform(-10, 10)))
        for i in range(20):
            events.append(_ev(820_000 + i * 10_000, "rpm", 800.0 + rng.uniform(-10, 10)))
        events.sort(key=lambda e: e.timestamp_us)
        dtc = DTCSnapshot(
            dtc_code="P0300",
            standard="obd2",
            timestamp_first_us=480_000,
            timestamp_latest_us=750_000,
            occurrence_count=1,
        )
        feats = build_pattern_features(events, dtc)
        assert any("dropped 4 time" in f for f in feats.notable_findings)
        for f in feats.notable_findings:
            assert any(ch.isdigit() for ch in f)


# ---------- property tests ----------


@given(
    values=st.lists(
        st.floats(min_value=-1000, max_value=1000, allow_nan=False), min_size=2, max_size=200
    ),
    period_us=st.integers(min_value=1_000, max_value=50_000),
)
@settings(max_examples=30, deadline=2_000)
def test_signal_summaries_handle_arbitrary_floats(values: list[float], period_us: int) -> None:
    events = [_ev(i * period_us, "x", v) for i, v in enumerate(values)]
    summaries = compute_signal_summaries(events, ["x"], window_us=period_us * len(values))
    assert len(summaries) == 1
    assert summaries[0].interval_stats.median_us == pytest.approx(period_us)
    assert summaries[0].transition_rate_hz >= 0


@given(
    values=st.lists(
        st.floats(min_value=0, max_value=100, allow_nan=False), min_size=4, max_size=200
    ),
)
@settings(max_examples=30, deadline=2_000)
def test_value_anomalies_never_crash(values: list[float]) -> None:
    events = [_ev(i * 10_000, "x", v) for i, v in enumerate(values)]
    detect_value_anomalies(events, "x")  # must not raise
