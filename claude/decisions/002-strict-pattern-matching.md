# ADR 002 — Strict (exact) pattern_id matching, not fuzzy ranking

**Status:** accepted (T0L.6)
**Date:** 2026-05-24

The recommender looks up `hypothesis.suggested_pattern_id` by exact equality
against the library index. It does **not** ask the LLM to rank library
matches semantically, and it does not fuzzy-match (Levenshtein, embedding
similarity, etc.) on the suggested ID.

Reasoning:

1. The LLM is already given the full `available_pattern_ids` list in its
   prompt, so the burden of mapping a hypothesis to an ID lies upstream
   where the model has full context. A second fuzzy layer downstream
   would just hide that the prompt did its job poorly.
2. Strict matching makes the report deterministic given the LLM output —
   no surprise pattern substitutions appear in the audit bundle.
3. Unknown IDs are logged as warnings and dropped, rather than raising,
   so one bad hypothesis cannot kill the whole report.

Trade-off: a typo or near-miss from the model produces no match. This is
acceptable because the prompt explicitly enumerates the valid IDs.

Phase 0 may add `parameter_suggestions.suggested_value` computation off the
`features` object — the recommender signature already accepts `features`
for this reason, even though Phase 0-Lite only emits `rationale`.
