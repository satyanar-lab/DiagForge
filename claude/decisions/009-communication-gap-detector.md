# ADR 009 — Communication-gap detector heuristic

**Status:** accepted (T0.4)
**Date:** 2026-05-24

T0.4 added a new `communication_gap` anomaly type to support lost-comm
U-codes (U0100 family) where the symptom is *missing frames* rather than
*bad signal values*. The detector needs to distinguish "normal jitter on
a periodic publisher" from "the source ECU went quiet for hundreds of
milliseconds."

The heuristic:
1. Compute the median inter-sample interval on a per-signal basis.
2. Threshold = `max(5 × median_interval, 50 ms)`.
3. Any interval exceeding the threshold becomes one `communication_gap`
   anomaly carrying its duration and the median publish interval in the
   description.

Why 5×: a healthy bus with bounded jitter rarely exceeds 2× its publish
period. A 5× threshold absorbs occasional bursty contention while still
catching the 200-400 ms outages typical of a lost-comm DTC. The 50 ms
floor stops slow signals (e.g. 1 Hz publishers) from triggering on
single-period jitter.

Trade-off: a publisher whose period legitimately varies by 5× (e.g. an
event-driven message that can sit idle for seconds) will be falsely
flagged. We don't currently have a way to know "this signal is allowed to
go quiet" beyond DBC metadata we don't yet ingest. Phase 1 may add a
per-signal expected-period hint, fed from DBC cycle-time attributes.

Companion change: the mitigation recommender's `_suggest_communication_retry`
parses both the gap duration and the median publish interval out of these
descriptions to compute `timeout_ms` (3 × publish), `clear_holdoff_ms`
(`max(200ms, 5 × publish)`), and `max_consecutive_misses` (3 by default,
4 when gaps exceed 6× publish). The detector and the recommender share
the regex contract; tests pin the description format to keep them in
sync.
