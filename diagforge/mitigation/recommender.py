"""Mitigation recommender — pairs hypotheses with library patterns.

Pattern matching is strict in Phase 0-Lite: only the exact `suggested_pattern_id`
the LLM emitted is looked up. Unknown IDs are dropped from the output (we log
a warning rather than throwing, so a single bad ID does not blow up the whole
report). Parameter suggestions are emitted as rationale text only — actually
computing the numbers per signal feature is a Phase 0 task (ADR-002).
"""

from __future__ import annotations

from diagforge._logging import get_logger
from diagforge.mitigation.library import MitigationLibrary
from diagforge.report.models import (
    DiagnosticResult,
    MitigationMatch,
    ParameterSuggestion,
    PatternFeatures,
)

_log = get_logger(__name__)


class MitigationRecommender:
    """Stateless wrapper around the library, used per DTC analysis."""

    def __init__(self, library: MitigationLibrary) -> None:
        self._lib = library

    def match(
        self,
        diagnostic_result: DiagnosticResult,
        features: PatternFeatures,
    ) -> list[MitigationMatch]:
        # features is reserved for value-derived suggestions in Phase 0 (ADR-002).
        _ = features
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
                    parameter_suggestions=[
                        ParameterSuggestion(
                            name=p.name,
                            suggested_value=None,
                            rationale=p.suggestion_rule,
                        )
                        for p in pattern.parameters
                    ],
                    verification_steps=list(pattern.verification),
                    standards_references=list(pattern.standards),
                )
            )
        return matches
