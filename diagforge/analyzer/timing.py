"""Deterministic timing and value-anomaly analysis (Layer 2).

The analyzer operates on decoded TraceEvents (i.e. ``event.decoded_signals``
populated by the ingestion CLI). It is intentionally LLM-free: same input
always produces the same output. The only baseline statistic used for value
anomaly detection is median + MAD, because real ECU signal data is rarely
Gaussian (idle wobble, key-cycle steps, sensor stair-stepping) and the mean
+ stddev estimator gets dragged around by the outliers it is supposed to
flag.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from itertools import pairwise

from diagforge.ingestion.models import DTCSnapshot, TraceEvent
from diagforge.report.models import (
    AnomalyType,
    IntervalStats,
    PatternFeatures,
    SignalSummary,
    TransitionAnomaly,
)

# Scale factor that makes 1.4826 * MAD a consistent estimator of stddev for a
# normal distribution. Real signals are non-Gaussian so the constant is a
# conservative-by-default mapping rather than a guarantee.
_MAD_TO_SIGMA = 1.4826


def _samples(events: Sequence[TraceEvent], signal_name: str) -> list[tuple[int, float]]:
    """Return (timestamp_us, value) for every event that carries `signal_name`."""
    out: list[tuple[int, float]] = []
    for ev in events:
        if ev.decoded_signals and signal_name in ev.decoded_signals:
            out.append((ev.timestamp_us, float(ev.decoded_signals[signal_name])))
    return out


def _intervals_us(samples: Sequence[tuple[int, float]]) -> list[int]:
    return [b[0] - a[0] for a, b in pairwise(samples)]


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)


def _mad(values: Sequence[float], median: float) -> float:
    if not values:
        return 0.0
    deviations = [abs(v - median) for v in values]
    return statistics.median(deviations)


def compute_signal_summaries(
    events: Sequence[TraceEvent],
    signals_of_interest: Iterable[str],
    window_us: int,
) -> list[SignalSummary]:
    """Per-signal frequency and inter-sample interval statistics."""
    out: list[SignalSummary] = []
    window_s = max(window_us / 1_000_000.0, 1e-9)
    for sig in signals_of_interest:
        samples = _samples(events, sig)
        if not samples:
            continue
        # Count value changes ("transitions"). transition_rate_hz reports those
        # transitions per second of the analysis window, not per sample.
        transitions = sum(1 for a, b in pairwise(samples) if a[1] != b[1])
        intervals = _intervals_us(samples)
        if intervals:
            stats = IntervalStats(
                mean_us=float(statistics.fmean(intervals)),
                median_us=float(statistics.median(intervals)),
                p99_us=float(_percentile([float(i) for i in intervals], 0.99)),
            )
        else:
            stats = IntervalStats()
        out.append(
            SignalSummary(
                signal_name=sig,
                transition_rate_hz=transitions / window_s,
                interval_stats=stats,
            )
        )
    return out


def detect_transition_anomalies(
    events: Sequence[TraceEvent],
    signal_name: str,
    window_us: int = 50_000,
    transition_threshold: int = 5,
) -> list[TransitionAnomaly]:
    """Flag sub-windows where the signal changes more often than expected.

    Sliding window of `window_us` over the samples. If any window contains
    more than `transition_threshold` value-changes, that window is reported
    as a `debounce_candidate` anomaly.
    """
    samples = _samples(events, signal_name)
    if len(samples) < 2:
        return []

    transitions: list[int] = [
        b[0] for a, b in pairwise(samples) if a[1] != b[1]
    ]  # timestamps of changes
    if not transitions:
        return []

    anomalies: list[TransitionAnomaly] = []
    seen_starts: set[int] = set()  # avoid emitting overlapping windows starting at the same change

    for i, start_ts in enumerate(transitions):
        # Count transitions within [start_ts, start_ts + window_us].
        end_ts = start_ts + window_us
        j = i
        while j < len(transitions) and transitions[j] <= end_ts:
            j += 1
        count = j - i
        if count > transition_threshold and start_ts not in seen_starts:
            anomalies.append(
                TransitionAnomaly(
                    signal_name=signal_name,
                    anomaly_type="debounce_candidate",
                    description=(
                        f"{signal_name} transitioned {count} times within "
                        f"{window_us // 1000}ms (threshold {transition_threshold})"
                    ),
                    evidence_us=transitions[i:j],
                )
            )
            # skip ahead by the burst so we don't emit a duplicate for every starting transition
            seen_starts.update(transitions[i:j])
    return anomalies


def detect_value_anomalies(
    events: Sequence[TraceEvent],
    signal_name: str,
    window_us: int = 200_000,
    deviation_sigmas: float = 3.0,
    min_anomaly_duration_us: int = 5_000,
) -> list[TransitionAnomaly]:
    """Flag samples whose value deviates from the median+MAD baseline.

    Returns one anomaly per contiguous run of outliers whose total duration is
    at least `min_anomaly_duration_us`. Single-sample dropouts at typical
    10ms framing satisfy a 5ms threshold; lower the threshold to catch
    sub-frame anomalies on high-rate signals.
    """
    samples = _samples(events, signal_name)
    if len(samples) < 4:
        return []

    values = [v for _, v in samples]
    median = statistics.median(values)
    mad = _mad(values, median)
    if mad == 0:
        # Degenerate: a perfectly flat signal has no scale; use a tiny positive
        # threshold so we don't divide by zero downstream and so identical
        # values are never flagged.
        return []

    threshold = deviation_sigmas * _MAD_TO_SIGMA * mad

    runs: list[list[tuple[int, float]]] = []
    cur: list[tuple[int, float]] = []
    for ts, v in samples:
        if abs(v - median) > threshold:
            cur.append((ts, v))
        else:
            if cur:
                runs.append(cur)
                cur = []
    if cur:
        runs.append(cur)

    # Estimate a per-sample period so that single-sample runs get a sensible duration.
    intervals = _intervals_us(samples)
    sample_period_us = int(statistics.median(intervals)) if intervals else 0

    anomalies: list[TransitionAnomaly] = []
    for run in runs:
        ts_first = run[0][0]
        ts_last = run[-1][0]
        # Run duration covers the *occupied* time of the dropout including the
        # implicit tail of the last sample.
        duration_us = (ts_last - ts_first) + sample_period_us
        if duration_us < min_anomaly_duration_us:
            continue
        run_values = [v for _, v in run]
        extreme = min(run_values) if statistics.fmean(run_values) < median else max(run_values)
        anomaly_type: AnomalyType
        if extreme < median:
            anomaly_type = "signal_dropout"
            verb = "dropped to"
        else:
            anomaly_type = "value_spike"
            verb = "spiked to"
        anomalies.append(
            TransitionAnomaly(
                signal_name=signal_name,
                anomaly_type=anomaly_type,
                description=(
                    f"{signal_name} {verb} "
                    f"{extreme:.0f} (baseline median {median:.0f}, MAD {mad:.1f}) for "
                    f"{duration_us / 1000:.0f}ms; cluster window {window_us // 1000}ms"
                ),
                evidence_us=[ts for ts, _ in run],
            )
        )
    return anomalies


def detect_power_cycle_bursts(
    events: Sequence[TraceEvent],
    supply_signal_name: str,
    window_us: int = 200_000,
) -> list[TransitionAnomaly]:
    """Detect supply-rail collapses that cluster within `window_us`.

    Heuristic: treat any value below 25% of the rolling median for the supply
    signal as a "power cycle event"; if two or more such events occur within
    `window_us` of each other, emit a `power_cycle_burst` anomaly.
    """
    samples = _samples(events, supply_signal_name)
    if len(samples) < 4:
        return []

    median = statistics.median(v for _, v in samples)
    if median <= 0:
        return []
    threshold = 0.25 * median
    collapses = [ts for ts, v in samples if v < threshold]
    if len(collapses) < 2:
        return []

    bursts: list[list[int]] = []
    cur: list[int] = [collapses[0]]
    for ts in collapses[1:]:
        if ts - cur[-1] <= window_us:
            cur.append(ts)
        else:
            if len(cur) >= 2:
                bursts.append(cur)
            cur = [ts]
    if len(cur) >= 2:
        bursts.append(cur)

    return [
        TransitionAnomaly(
            signal_name=supply_signal_name,
            anomaly_type="power_cycle_burst",
            description=(
                f"{supply_signal_name} collapsed below {threshold:.1f} "
                f"{len(burst)} times within {(burst[-1] - burst[0]) / 1000:.0f}ms "
                f"(supply baseline {median:.1f})"
            ),
            evidence_us=burst,
        )
        for burst in bursts
    ]


def _slice_to_window(
    events: Sequence[TraceEvent], dtc: DTCSnapshot, window_us: int
) -> list[TraceEvent]:
    """Return events whose timestamps fall within `window_us` before or after the DTC range."""
    lo = max(0, dtc.timestamp_first_us - window_us)
    hi = dtc.timestamp_latest_us + window_us
    return [ev for ev in events if lo <= ev.timestamp_us <= hi]


def _discover_signals(events: Sequence[TraceEvent]) -> list[str]:
    """Return the union of decoded signal names seen across events, in stable order."""
    seen: dict[str, None] = {}
    for ev in events:
        if ev.decoded_signals:
            for name in ev.decoded_signals:
                if name not in seen:
                    seen[name] = None
    return list(seen)


def build_pattern_features(
    events: Sequence[TraceEvent],
    dtc_snapshot: DTCSnapshot,
    window_us: int = 500_000,
) -> PatternFeatures:
    """Compose signal summaries, transition + value anomalies, notable findings.

    The output is what Layer 3 (the diagnostic agent) reasons over. Notable
    findings are short, number-bearing strings the LLM is required to cite
    verbatim — never invent — as evidence in its hypotheses.
    """
    window = _slice_to_window(events, dtc_snapshot, window_us)
    signals = _discover_signals(window)

    summaries = compute_signal_summaries(window, signals, window_us=window_us * 2)

    transitions: list[TransitionAnomaly] = []
    for s in signals:
        transitions.extend(detect_transition_anomalies(window, s))

    values: list[TransitionAnomaly] = []
    for s in signals:
        values.extend(detect_value_anomalies(window, s, window_us=window_us))

    all_anomalies = transitions + values

    findings = _build_notable_findings(values, transitions, dtc_snapshot, window_us)

    return PatternFeatures(
        window_us=window_us,
        signal_summaries=summaries,
        transition_anomalies=all_anomalies,
        correlations=[],
        notable_findings=findings,
    )


def _group_runs_by_signal(
    value_anomalies: Sequence[TransitionAnomaly],
) -> dict[str, list[TransitionAnomaly]]:
    grouped: dict[str, list[TransitionAnomaly]] = defaultdict(list)
    for a in value_anomalies:
        grouped[a.signal_name].append(a)
    return grouped


def _build_notable_findings(
    value_anomalies: Sequence[TransitionAnomaly],
    transition_anomalies: Sequence[TransitionAnomaly],
    dtc: DTCSnapshot,
    window_us: int,
) -> list[str]:
    """Render anomaly clusters into short, number-bearing English strings."""
    findings: list[str] = []

    for signal, runs in _group_runs_by_signal(value_anomalies).items():
        if not runs:
            continue
        dropouts = [a for a in runs if a.anomaly_type == "signal_dropout"]
        spikes = [a for a in runs if a.anomaly_type == "value_spike"]
        if dropouts:
            durations_ms = [_estimate_duration_ms(a) for a in dropouts]
            depths = [_extract_extreme(a, low=True) for a in dropouts]
            findings.append(
                f"{signal} dropped {len(dropouts)} time(s) within "
                f"{window_us // 1000}ms of the DTC window — "
                f"extreme values {sorted({round(d) for d in depths if d is not None})}, "
                f"durations (ms) {durations_ms}"
            )
        if spikes:
            durations_ms = [_estimate_duration_ms(a) for a in spikes]
            peaks = [_extract_extreme(a, low=False) for a in spikes]
            findings.append(
                f"{signal} spiked {len(spikes)} time(s) within "
                f"{window_us // 1000}ms of the DTC window — "
                f"peak values {sorted({round(p) for p in peaks if p is not None})}, "
                f"durations (ms) {durations_ms}"
            )

    for a in transition_anomalies:
        if a.anomaly_type == "debounce_candidate":
            findings.append(f"{a.signal_name} bounce burst: {a.description}")
        elif a.anomaly_type == "power_cycle_burst":
            findings.append(f"power-rail event: {a.description}")

    if not findings:
        findings.append(
            f"No value, transition, or supply anomalies detected for {dtc.dtc_code} "
            f"within ±{window_us // 1000}ms — DTC may reflect a steady-state condition."
        )
    return findings


def _estimate_duration_ms(anomaly: TransitionAnomaly) -> int:
    """Best-effort millisecond duration extracted from the anomaly description."""
    desc = anomaly.description
    for tok in desc.split():
        if tok.endswith("ms"):
            try:
                return int(round(float(tok[:-2])))
            except ValueError:
                continue
    return 0


def _extract_extreme(anomaly: TransitionAnomaly, low: bool) -> float | None:
    """Pull the extreme value out of the anomaly description (best-effort)."""
    tokens = anomaly.description.split()
    for i, tok in enumerate(tokens):
        if tok in ("to", "to:"):
            try:
                return float(tokens[i + 1])
            except (ValueError, IndexError):
                continue
    return None if low else None
