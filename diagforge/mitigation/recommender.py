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
from collections.abc import Callable

from diagforge._logging import get_logger
from diagforge.mitigation.library import MitigationLibrary, MitigationPattern, PatternParameter
from diagforge.report.models import (
    DiagnosticResult,
    MitigationMatch,
    ParameterSuggestion,
    PatternFeatures,
)

_log = get_logger(__name__)

# Pull "<N>ms" out of an anomaly description (e.g. "for 30ms; cluster window 500ms").
# The leading "for" anchor ensures we hit the per-anomaly duration, not the
# trailing "cluster window" or any other parenthetical ms token.
_DURATION_RE = re.compile(r"\bfor\s+(\d+(?:\.\d+)?)\s*ms\b", re.IGNORECASE)


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


# Patterns deferred to Phase 0: they require data the Phase 0-Lite analyzer
# does not yet produce — threshold-crossing distributions for the dematuration
# timer, NVM device tWR + transient error rate for the retry state machine,
# code-side bounds metadata for the boundary-condition guard.
_SuggesterFn = Callable[[PatternFeatures, list[PatternParameter]], list[ParameterSuggestion]]
_DISPATCH: dict[str, _SuggesterFn] = {
    "duration_qualified_debounce": _suggest_debounce,
    "plausibility_check_redundant_signals": _suggest_plausibility,
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
