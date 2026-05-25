"""Unit tests for the mitigation library and recommender."""

from __future__ import annotations

from pathlib import Path

import pytest

from diagforge.mitigation.library import MitigationLibrary, MitigationLibraryError
from diagforge.mitigation.recommender import MitigationRecommender
from diagforge.report.models import (
    DiagnosticResult,
    Hypothesis,
    PatternFeatures,
    TransitionAnomaly,
)


@pytest.fixture
def library() -> MitigationLibrary:
    return MitigationLibrary.from_packaged_data()


def _result(*pattern_ids: str | None) -> DiagnosticResult:
    return DiagnosticResult(
        hypotheses=[
            Hypothesis(
                rank=i + 1,
                description=f"h{i}",
                confidence="medium",
                evidence=["evidence-line"],
                suggested_pattern_id=pid,
                reasoning="r",
            )
            for i, pid in enumerate(pattern_ids)
        ],
        model="claude-sonnet-4-6",
        model_version="2026-01",
        prompt_template_version="diag-v1",
    )


def _features() -> PatternFeatures:
    return PatternFeatures(window_us=500_000, notable_findings=["evidence-line"])


def _dropout_anomaly(signal: str, duration_ms: int) -> TransitionAnomaly:
    return TransitionAnomaly(
        signal_name=signal,
        anomaly_type="signal_dropout",
        description=f"{signal} dropped to 50 (baseline median 802, MAD 5.1) for {duration_ms}ms",
        evidence_us=[],
    )


def _features_with_dropouts(durations_ms: list[int]) -> PatternFeatures:
    return PatternFeatures(
        window_us=500_000,
        transition_anomalies=[_dropout_anomaly("engine_rpm", d) for d in durations_ms],
        notable_findings=[f"engine_rpm dropped {len(durations_ms)} time(s)"],
    )


# ---------- library ----------


class TestLibraryLoading:
    def test_starter_library_loads_all_patterns(self, library: MitigationLibrary) -> None:
        ids = library.list_pattern_ids()
        assert len(ids) == 10
        assert set(ids) == {
            "boundary_condition_guard",
            "communication_retry_state_machine",
            "cross_ecu_consensus",
            "dematuration_timer",
            "duration_qualified_debounce",
            "gradient_limit_check",
            "oscillation_hysteresis",
            "plausibility_check_redundant_signals",
            "retry_state_machine_nvm",
            "signal_freshness_check",
        }

    @pytest.mark.parametrize(
        "pattern_id",
        [
            "communication_retry_state_machine",
            "oscillation_hysteresis",
            "signal_freshness_check",
            "gradient_limit_check",
            "cross_ecu_consensus",
        ],
    )
    def test_new_pattern_has_full_schema(self, library: MitigationLibrary, pattern_id: str) -> None:
        p = library.get_by_id(pattern_id)
        assert p is not None
        assert p.name
        assert p.short_description
        assert len(p.when_applies) >= 2
        assert len(p.parameters) >= 2
        assert all(param.suggestion_rule for param in p.parameters)
        assert len(p.verification) >= 3
        assert len(p.standards) >= 1

    def test_get_by_id_known(self, library: MitigationLibrary) -> None:
        p = library.get_by_id("dematuration_timer")
        assert p is not None
        assert p.name == "Dematuration / fault-clear timer"
        assert any("dematuration_time_ms" in param.name for param in p.parameters)

    def test_get_by_id_unknown_returns_none(self, library: MitigationLibrary) -> None:
        assert library.get_by_id("not_a_real_pattern") is None

    def test_duplicate_id_in_a_file_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "dup.yaml"
        bad.write_text(
            "schema_version: '1.0.0'\n"
            "patterns:\n"
            "  - { pattern_id: a, name: a, short_description: s }\n"
            "  - { pattern_id: a, name: a2, short_description: s }\n"
        )
        with pytest.raises(MitigationLibraryError, match="duplicate pattern_id"):
            MitigationLibrary.from_paths([bad])

    def test_wrong_schema_version_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "v2.yaml"
        bad.write_text("schema_version: '2.0.0'\npatterns: []\n")
        with pytest.raises(MitigationLibraryError, match="unsupported schema_version"):
            MitigationLibrary.from_paths([bad])

    def test_malformed_yaml_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("schema_version: '1.0.0'\npatterns: [: : :")
        with pytest.raises(MitigationLibraryError, match="YAML parse error"):
            MitigationLibrary.from_paths([bad])

    def test_pattern_missing_required_field_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "x.yaml"
        bad.write_text(
            "schema_version: '1.0.0'\n"
            "patterns:\n"
            "  - { pattern_id: x }\n"  # no name, no short_description
        )
        with pytest.raises(MitigationLibraryError, match="failed validation"):
            MitigationLibrary.from_paths([bad])

    def test_non_mapping_root_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "x.yaml"
        bad.write_text("- not a mapping\n")
        with pytest.raises(MitigationLibraryError, match="must be a mapping"):
            MitigationLibrary.from_paths([bad])


# ---------- recommender ----------


class TestRecommender:
    def test_match_single_hypothesis(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        matches = rec.match(_result("dematuration_timer"), _features())
        assert len(matches) == 1
        assert matches[0].pattern_id == "dematuration_timer"
        assert matches[0].verification_steps
        assert matches[0].standards_references

    def test_match_drops_unknown_pattern_id(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        matches = rec.match(_result("totally_made_up", "dematuration_timer"), _features())
        assert len(matches) == 1
        assert matches[0].pattern_id == "dematuration_timer"

    def test_match_dedupes_repeated_pattern_id(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        matches = rec.match(_result("dematuration_timer", "dematuration_timer"), _features())
        assert len(matches) == 1

    def test_match_skips_null_pattern_id(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        matches = rec.match(_result(None, "dematuration_timer"), _features())
        assert len(matches) == 1

    def test_no_hypotheses_returns_empty(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        empty = DiagnosticResult(
            hypotheses=[
                Hypothesis(
                    rank=1,
                    description="x",
                    confidence="low",
                    evidence=["e"],
                    suggested_pattern_id=None,
                    reasoning="r",
                )
            ],
            model="claude-sonnet-4-6",
            model_version="2026-01",
            prompt_template_version="diag-v1",
        )
        assert rec.match(empty, _features()) == []


# ---------- computed parameter suggestions ----------


def _param(match: object, name: str) -> object:
    """Pull a named parameter suggestion off a MitigationMatch (test helper)."""
    assert hasattr(match, "parameter_suggestions")
    for p in match.parameter_suggestions:  # type: ignore[attr-defined]
        if p.name == name:
            return p
    raise AssertionError(f"no parameter named {name!r}")


class TestDebounceComputedValues:
    def test_qualification_time_from_dropouts_p0300_shape(self, library: MitigationLibrary) -> None:
        # The P0300 demo produces 30/30/30/29 ms dropouts; max=30 → 60ms (already on 5ms boundary).
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([30, 30, 30, 29])
        matches = rec.match(_result("duration_qualified_debounce"), feats)
        qt = _param(matches[0], "qualification_time_ms")
        assert qt.suggested_value == 60  # type: ignore[attr-defined]
        assert "max(30, 30, 30, 29)" in qt.rationale  # type: ignore[attr-defined]
        assert "× 2 = 60ms" in qt.rationale  # type: ignore[attr-defined]

    def test_rounding_up_to_next_5ms_boundary(self, library: MitigationLibrary) -> None:
        # max 22ms × 2 = 44ms → rounds up to 45ms.
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([18, 22, 19])
        matches = rec.match(_result("duration_qualified_debounce"), feats)
        qt = _param(matches[0], "qualification_time_ms")
        assert qt.suggested_value == 45  # type: ignore[attr-defined]
        assert "44ms" in qt.rationale  # type: ignore[attr-defined]
        assert "→ 45ms" in qt.rationale  # type: ignore[attr-defined]

    def test_exact_multiple_of_5_unchanged(self, library: MitigationLibrary) -> None:
        # max 5ms × 2 = 10ms, already a 5ms multiple.
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([5, 5])
        matches = rec.match(_result("duration_qualified_debounce"), feats)
        qt = _param(matches[0], "qualification_time_ms")
        assert qt.suggested_value == 10  # type: ignore[attr-defined]

    def test_confirmation_count_defaults_to_1(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([30])
        matches = rec.match(_result("duration_qualified_debounce"), feats)
        cc = _param(matches[0], "confirmation_count")
        assert cc.suggested_value == 1  # type: ignore[attr-defined]

    def test_no_dropouts_leaves_qualification_time_unset(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        feats = PatternFeatures(window_us=500_000, notable_findings=["nothing observed"])
        matches = rec.match(_result("duration_qualified_debounce"), feats)
        qt = _param(matches[0], "qualification_time_ms")
        assert qt.suggested_value is None  # type: ignore[attr-defined]
        # The YAML rule text is still preserved as the rationale.
        assert "bounce interval" in qt.rationale  # type: ignore[attr-defined]


class TestPlausibilityComputedValues:
    def test_tolerance_window_from_max_dropout_duration(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([30, 35, 28])  # max = 35
        matches = rec.match(_result("plausibility_check_redundant_signals"), feats)
        tw = _param(matches[0], "tolerance_window_ms")
        assert tw.suggested_value == 55  # 35 + 20  # type: ignore[attr-defined]
        assert "35ms + 20ms buffer = 55ms" in tw.rationale  # type: ignore[attr-defined]

    def test_disagreement_threshold_remains_unset(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([30])
        matches = rec.match(_result("plausibility_check_redundant_signals"), feats)
        dt = _param(matches[0], "disagreement_threshold")
        assert dt.suggested_value is None  # type: ignore[attr-defined]

    def test_no_dropouts_leaves_tolerance_window_unset(self, library: MitigationLibrary) -> None:
        rec = MitigationRecommender(library)
        feats = PatternFeatures(window_us=500_000, notable_findings=["nothing observed"])
        matches = rec.match(_result("plausibility_check_redundant_signals"), feats)
        tw = _param(matches[0], "tolerance_window_ms")
        assert tw.suggested_value is None  # type: ignore[attr-defined]


class TestDeferredPatternsRemainNull:
    """Patterns Phase 0-Lite cannot yet parameterize must still emit None values."""

    @pytest.mark.parametrize(
        "pattern_id",
        ["dematuration_timer", "retry_state_machine_nvm", "boundary_condition_guard"],
    )
    def test_all_parameters_unset(self, library: MitigationLibrary, pattern_id: str) -> None:
        rec = MitigationRecommender(library)
        feats = _features_with_dropouts([30, 30, 30, 29])
        matches = rec.match(_result(pattern_id), feats)
        assert all(p.suggested_value is None for p in matches[0].parameter_suggestions)
        # rationale strings remain populated from the YAML
        assert all(p.rationale for p in matches[0].parameter_suggestions)
