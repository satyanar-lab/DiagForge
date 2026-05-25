"""Multi-DTC correlation — co-occurrence and causal ordering across DTCs.

Each DTC snapshot only carries `timestamp_first_us`, `timestamp_latest_us`,
and `occurrence_count`. We cannot observe per-occurrence timestamps in
Phase 1, so the ordering heuristic uses first/last endpoints as a coarse
two-point proxy: if DTC A's first-seen precedes B's by more than the
co-occurrence window, AND A's last-seen still precedes B's, then A is
likely an antecedent of B for at least two occurrences. A stronger
per-occurrence-ordering check is a Phase 2 task (requires the snapshot
format to carry per-occurrence timestamps).
"""

from __future__ import annotations

from collections.abc import Sequence

from diagforge.ingestion.models import DTCSnapshot
from diagforge.report.models import CrossDtcFinding

#: Two DTCs that set within this window of each other are flagged as
#: co-occurring (likely causally linked or sharing a common root cause).
DEFAULT_CO_OCCURRENCE_WINDOW_US = 100_000


def detect_findings(
    dtcs: Sequence[DTCSnapshot],
    co_occurrence_window_us: int = DEFAULT_CO_OCCURRENCE_WINDOW_US,
) -> list[CrossDtcFinding]:
    """Emit cross-DTC findings for every relevant pair of DTCs.

    A pair (A, B) yields at most one finding:
    * `co_occurring` if |A.first - B.first| ≤ co_occurrence_window_us.
    * `causal_ordering` otherwise, when both DTCs have occurrence_count > 1
      and the earlier one's last-seen still precedes the later one's
      last-seen (so the ordering held across at least two events).

    The list is empty for single-DTC inputs.
    """
    findings: list[CrossDtcFinding] = []
    n = len(dtcs)
    if n < 2:
        return findings

    for i in range(n):
        for j in range(i + 1, n):
            a, b = dtcs[i], dtcs[j]
            first_delta = abs(a.timestamp_first_us - b.timestamp_first_us)

            if first_delta <= co_occurrence_window_us:
                findings.append(
                    CrossDtcFinding(
                        type="co_occurring",
                        dtc_codes=[a.dtc_code, b.dtc_code],
                        description=(
                            f"{a.dtc_code} and {b.dtc_code} both set within "
                            f"{first_delta / 1000:.0f}ms of each other — likely "
                            "causally linked or sharing a common root cause."
                        ),
                        delta_us=first_delta,
                    )
                )
                continue

            if a.occurrence_count > 1 and b.occurrence_count > 1:
                if a.timestamp_first_us < b.timestamp_first_us:
                    earlier, later = a, b
                else:
                    earlier, later = b, a
                first_lag = later.timestamp_first_us - earlier.timestamp_first_us
                last_lag = later.timestamp_latest_us - earlier.timestamp_latest_us
                if last_lag <= 0:
                    # Ordering didn't hold across both endpoints — skip.
                    continue
                findings.append(
                    CrossDtcFinding(
                        type="causal_ordering",
                        dtc_codes=[earlier.dtc_code, later.dtc_code],
                        description=(
                            f"{earlier.dtc_code} consistently precedes "
                            f"{later.dtc_code} — first-occurrence lag "
                            f"{first_lag / 1000:.0f}ms, latest-occurrence lag "
                            f"{last_lag / 1000:.0f}ms across "
                            f"{min(earlier.occurrence_count, later.occurrence_count)}+ "
                            f"events. {earlier.dtc_code} may be a cause."
                        ),
                        delta_us=first_lag,
                    )
                )
    return findings
