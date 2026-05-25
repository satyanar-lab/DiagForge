"""Tests for the cross-DTC correlation analyzer."""

from __future__ import annotations

from diagforge.analyzer.cross_dtc import detect_findings
from diagforge.ingestion.models import DTCSnapshot


def _dtc(
    code: str,
    first_us: int,
    latest_us: int | None = None,
    count: int = 1,
) -> DTCSnapshot:
    return DTCSnapshot(
        dtc_code=code,
        standard="obd2",
        timestamp_first_us=first_us,
        timestamp_latest_us=latest_us if latest_us is not None else first_us,
        occurrence_count=count,
    )


class TestEmptyAndTrivial:
    def test_single_dtc_yields_no_findings(self) -> None:
        assert detect_findings([_dtc("P0420", 100_000)]) == []

    def test_empty_input(self) -> None:
        assert detect_findings([]) == []


class TestCoOccurrence:
    def test_two_dtcs_within_window_flagged(self) -> None:
        out = detect_findings([_dtc("P0420", 500_000), _dtc("P0430", 530_000)])
        assert len(out) == 1
        assert out[0].type == "co_occurring"
        assert set(out[0].dtc_codes) == {"P0420", "P0430"}
        assert out[0].delta_us == 30_000

    def test_outside_window_not_co_occurring(self) -> None:
        out = detect_findings([_dtc("P0420", 100_000), _dtc("P0430", 500_000)])
        # Not co-occurring, but no causal-ordering either since both have count=1.
        assert all(o.type != "co_occurring" for o in out)

    def test_co_occurrence_window_configurable(self) -> None:
        out = detect_findings(
            [_dtc("A", 0), _dtc("B", 50_000)],
            co_occurrence_window_us=10_000,
        )
        assert out == []


class TestCausalOrdering:
    def test_repeated_ordering_flagged(self) -> None:
        out = detect_findings(
            [
                _dtc("U0100", 200_000, latest_us=1_500_000, count=3),
                _dtc("P0700", 350_000, latest_us=1_650_000, count=3),
            ]
        )
        assert len(out) == 1
        assert out[0].type == "causal_ordering"
        assert out[0].dtc_codes == ["U0100", "P0700"]
        assert out[0].delta_us == 150_000

    def test_ordering_does_not_hold_on_second_endpoint(self) -> None:
        # First-occurrence lag positive but latest-occurrence reversed → skip.
        out = detect_findings(
            [
                _dtc("A", 0, latest_us=2_000_000, count=2),
                _dtc("B", 200_000, latest_us=1_500_000, count=2),
            ]
        )
        assert out == []

    def test_single_occurrence_not_causal(self) -> None:
        out = detect_findings(
            [
                _dtc("A", 0, latest_us=0, count=1),
                _dtc("B", 500_000, latest_us=500_000, count=1),
            ]
        )
        assert out == []


class TestPairwiseEnumeration:
    def test_three_dtc_all_pairs_evaluated(self) -> None:
        out = detect_findings(
            [
                _dtc("A", 0),
                _dtc("B", 50_000),
                _dtc("C", 80_000),
            ]
        )
        # Three pairs all within 100ms window: A-B (50ms), A-C (80ms), B-C (30ms).
        assert len(out) == 3
        assert all(o.type == "co_occurring" for o in out)
