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


# ---------- library ----------


class TestLibraryLoading:
    def test_starter_library_loads_all_five_patterns(self, library: MitigationLibrary) -> None:
        ids = library.list_pattern_ids()
        assert len(ids) == 5
        assert set(ids) == {
            "boundary_condition_guard",
            "dematuration_timer",
            "duration_qualified_debounce",
            "plausibility_check_redundant_signals",
            "retry_state_machine_nvm",
        }

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
