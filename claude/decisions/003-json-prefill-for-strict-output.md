# ADR 003 — JSON pre-fill for strict structured output

**Status:** **Superseded by ADR-007** (Claude 4-series models reject
assistant-message pre-fill). Originally accepted at T0L.5; replaced by
tool_use + forced `tool_choice` in the post-T0L.8 fix bundle.
**Date:** 2026-05-24

The Anthropic SDK does not currently expose a dedicated `response_format`
toggle the way some other vendors do. To force the model into JSON-only
output we use **assistant pre-fill**: the conversation is constructed as
`[user: <prompt>, assistant: "{"]` so the model continues from an open brace
and cannot decide to wrap the response in prose or a markdown fence.

The raw response from the wire is concatenated with the leading `{`, then
defensively stripped of any trailing ``` fence the model still emits despite
the system-prompt instructions. The combined string goes through `json.loads`
and pydantic validation. Failures log a 500-char truncation of the raw body
and raise `DiagnosticParseError` — the silent-drop antipattern is explicitly
forbidden by CLAUDE.md rule 3.

Trade-off: pre-fill costs us the ability to detect "the model wanted to say
no" at the protocol layer — the model will always produce *something*
starting with `{`. We accept this because every downstream step then runs
pydantic validation, and a hallucinated `{"hypotheses": []}` already raises
`DiagnosticParseError` ("zero hypotheses returned").
