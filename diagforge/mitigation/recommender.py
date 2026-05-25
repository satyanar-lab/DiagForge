"""Mitigation recommender — pairs hypotheses with library patterns.

Pattern matching is strict in Phase 0-Lite: only the exact `suggested_pattern_id`
the LLM emitted is looked up. Unknown IDs are dropped from the output (we log
a warning rather than throwing, so a single bad ID does not blow up the whole
report).

For two of the five starter patterns (`duration_qualified_debounce`,
`plausibility_check_redundant_signals`) the recommender now derives concrete
numeric parameter suggestions from the analyzer output. The remaining three
patterns (`dematuration_timer`, `retry_state_machine_nvm`,
`boundary_condition_guard`) require data we do not yet extract — threshold
crossings, NVM device characteristics, code structure — and are deferred to
Phase 0; their rationale text from the YAML library is still emitted.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Callable
from itertools import pairwise

from diagforge._logging import get_logger
from diagforge.mitigation.library import MitigationLibrary, MitigationPattern, PatternParameter
from diagforge.report.models import (
    DiagnosticResult,
    MitigationMatch,
    ParameterSuggestion,
    PatternFeatures,
)

_log = get_logger(__name__)

# Pull "<N>ms" out of an anomaly description (e.g. "for 30ms; cluster window 500ms"
# or "silent for 320ms"). The leading anchor ensures we hit the per-anomaly
# duration, not the trailing "cluster window" or any other parenthetical ms token.
_DURATION_RE = re.compile(r"\b(?:for|silent for)\s+(\d+(?:\.\d+)?)\s*ms\b", re.IGNORECASE)
_PUBLISH_INTERVAL_RE = re.compile(r"median publish interval\s+(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


def _extract_dropout_durations_ms(features: PatternFeatures) -> list[int]:
    """Return per-dropout durations (ms) parsed from signal_dropout anomalies."""
    out: list[int] = []
    for a in features.transition_anomalies:
        if a.anomaly_type != "signal_dropout":
            continue
        match = _DURATION_RE.search(a.description)
        if match is None:
            continue
        out.append(int(round(float(match.group(1)))))
    return out


def _extract_gap_durations_ms(features: PatternFeatures) -> list[int]:
    """Return per-gap durations (ms) parsed from communication_gap anomalies."""
    out: list[int] = []
    for a in features.transition_anomalies:
        if a.anomaly_type != "communication_gap":
            continue
        match = _DURATION_RE.search(a.description)
        if match is None:
            continue
        out.append(int(round(float(match.group(1)))))
    return out


def _extract_publish_interval_ms(features: PatternFeatures) -> float | None:
    """Pull the median publish interval (ms) out of the first communication_gap anomaly."""
    for a in features.transition_anomalies:
        if a.anomaly_type != "communication_gap":
            continue
        match = _PUBLISH_INTERVAL_RE.search(a.description)
        if match is not None:
            return float(match.group(1))
    return None


def _round_up_to_nearest(value_ms: int, step_ms: int) -> int:
    return int(math.ceil(value_ms / step_ms) * step_ms)


def _default_suggestion(p: PatternParameter) -> ParameterSuggestion:
    return ParameterSuggestion(name=p.name, suggested_value=None, rationale=p.suggestion_rule)


def _suggest_debounce(
    features: PatternFeatures, params: list[PatternParameter]
) -> list[ParameterSuggestion]:
    durations = _extract_dropout_durations_ms(features)
    out: list[ParameterSuggestion] = []
    for p in params:
        if p.name == "qualification_time_ms":
            if not durations:
                out.append(_default_suggestion(p))
                continue
            worst = max(durations)
            doubled = worst * 2
            rounded = _round_up_to_nearest(doubled, 5)
            values_str = ", ".join(str(d) for d in durations)
            rationale = (
                f"max({values_str}) ms × 2 = {doubled}ms "
                f"(rounded up to nearest 5ms → {rounded}ms)"
            )
            out.append(
                ParameterSuggestion(name=p.name, suggested_value=rounded, rationale=rationale)
            )
        elif p.name == "confirmation_count":
            out.append(
                ParameterSuggestion(
                    name=p.name,
                    suggested_value=1,
                    rationale=(
                        "Default 1; raise to 2 if noise persists across the qualification window."
                    ),
                )
            )
        else:
            out.append(_default_suggestion(p))
    return out


def _suggest_plausibility(
    features: PatternFeatures, params: list[PatternParameter]
) -> list[ParameterSuggestion]:
    durations = _extract_dropout_durations_ms(features)
    out: list[ParameterSuggestion] = []
    for p in params:
        if p.name == "tolerance_window_ms" and durations:
            worst = max(durations)
            value = worst + 20
            rationale = (
                f"max observed disturbance {worst}ms + 20ms buffer = {value}ms "
                "(cover sensor propagation delay + bus latency)"
            )
            out.append(ParameterSuggestion(name=p.name, suggested_value=value, rationale=rationale))
        else:
            # disagreement_threshold is domain-specific / categorical — leave
            # rationale-only; same for tolerance_window_ms when no dropouts.
            out.append(_default_suggestion(p))
    return out


def _suggest_communication_retry(
    features: PatternFeatures, params: list[PatternParameter]
) -> list[ParameterSuggestion]:
    """Derive timeout_ms and clear_holdoff_ms from observed publish gaps."""
    gaps = _extract_gap_durations_ms(features)
    publish_interval = _extract_publish_interval_ms(features)
    out: list[ParameterSuggestion] = []
    for p in params:
        if p.name == "timeout_ms" and publish_interval is not None:
            # 3x publish interval, rounded up to nearest 10ms.
            value = int(math.ceil(publish_interval * 3 / 10) * 10)
            rationale = (
                f"3 × median publish interval ({publish_interval:.1f}ms) = "
                f"{publish_interval * 3:.1f}ms → rounded up to {value}ms"
            )
            out.append(ParameterSuggestion(name=p.name, suggested_value=value, rationale=rationale))
        elif p.name == "clear_holdoff_ms" and publish_interval is not None:
            value = max(200, int(math.ceil(publish_interval * 5 / 10) * 10))
            rationale = f"max(200ms, 5 × publish interval {publish_interval:.1f}ms) = {value}ms"
            out.append(ParameterSuggestion(name=p.name, suggested_value=value, rationale=rationale))
        elif p.name == "max_consecutive_misses" and gaps:
            # Suggest 3 by default; raise to 4 if we observed gaps wider than 4× publish interval.
            value = 4 if publish_interval and max(gaps) > 4 * publish_interval * 1.5 else 3
            rationale = (
                f"Default 3 from {len(gaps)} observed gap(s) of "
                f"{sorted(gaps)} ms; raise to 4 only on very bursty buses."
                if value == 3
                else (
                    f"4 because observed gaps {sorted(gaps)} ms exceed "
                    "6× publish interval — bus is unusually bursty."
                )
            )
            out.append(ParameterSuggestion(name=p.name, suggested_value=value, rationale=rationale))
        else:
            out.append(_default_suggestion(p))
    return out


def _suggest_dematuration_timer(
    features: PatternFeatures, params: list[PatternParameter]
) -> list[ParameterSuggestion]:
    """Derive dematuration_time_ms from oscillation period of value anomalies.

    When the analyzer sees a signal repeatedly crossing baseline (alternating
    spikes/dropouts), the dominant period is the cluster-to-cluster gap.
    The classical rule of thumb is dematuration_time_ms = 5 × dominant period.
    """
    value_anoms = [
        a
        for a in features.transition_anomalies
        if a.anomaly_type in ("signal_dropout", "value_spike")
    ]
    out: list[ParameterSuggestion] = []
    for p in params:
        if p.name == "dematuration_time_ms" and len(value_anoms) >= 2:
            # Use first-evidence timestamp of each anomaly to measure period.
            timestamps_us = sorted(a.evidence_us[0] for a in value_anoms if a.evidence_us)
            if len(timestamps_us) >= 2:
                intervals_us = [b - a for a, b in pairwise(timestamps_us)]
                period_ms = max(1, int(round(statistics.median(intervals_us) / 1000)))
                value = int(math.ceil(period_ms * 5 / 50) * 50)
                rationale = (
                    f"5 × median oscillation period ({period_ms}ms across "
                    f"{len(timestamps_us)} crossings) = {period_ms * 5}ms → "
                    f"rounded up to {value}ms"
                )
                out.append(
                    ParameterSuggestion(name=p.name, suggested_value=value, rationale=rationale)
                )
                continue
        out.append(_default_suggestion(p))
    return out


# Patterns deferred to later phases: they require data the analyzer does not
# yet produce — NVM device tWR + transient error rate for the retry state
# machine, code-side bounds metadata for the boundary-condition guard,
# noise-amplitude measurement for the oscillation_hysteresis band, etc.
_SuggesterFn = Callable[[PatternFeatures, list[PatternParameter]], list[ParameterSuggestion]]
_DISPATCH: dict[str, _SuggesterFn] = {
    "duration_qualified_debounce": _suggest_debounce,
    "plausibility_check_redundant_signals": _suggest_plausibility,
    "communication_retry_state_machine": _suggest_communication_retry,
    "dematuration_timer": _suggest_dematuration_timer,
}


def _build_suggestions(
    pattern: MitigationPattern, features: PatternFeatures
) -> list[ParameterSuggestion]:
    fn = _DISPATCH.get(pattern.pattern_id)
    if fn is None:
        return [_default_suggestion(p) for p in pattern.parameters]
    return fn(features, list(pattern.parameters))


class MitigationRecommender:
    """Stateless wrapper around the library, used per DTC analysis."""

    def __init__(self, library: MitigationLibrary) -> None:
        self._lib = library

    def match(
        self,
        diagnostic_result: DiagnosticResult,
        features: PatternFeatures,
    ) -> list[MitigationMatch]:
        seen: set[str] = set()
        matches: list[MitigationMatch] = []
        for h in diagnostic_result.hypotheses:
            if h.suggested_pattern_id is None or h.suggested_pattern_id in seen:
                continue
            pattern = self._lib.get_by_id(h.suggested_pattern_id)
            if pattern is None:
                _log.warning(
                    "hypothesis rank=%d cited unknown pattern_id '%s'; skipping",
                    h.rank,
                    h.suggested_pattern_id,
                )
                continue
            seen.add(h.suggested_pattern_id)
            matches.append(
                MitigationMatch(
                    pattern_id=pattern.pattern_id,
                    pattern_name=pattern.name,
                    parameter_suggestions=_build_suggestions(pattern, features),
                    verification_steps=list(pattern.verification),
                    standards_references=list(pattern.standards),
                )
            )
        return matches
