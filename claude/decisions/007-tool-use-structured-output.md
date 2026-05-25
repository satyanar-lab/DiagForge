# ADR 007 — Tool_use with forced tool_choice for structured output

**Status:** accepted (post-T0L.8 fix)
**Date:** 2026-05-24
**Supersedes:** ADR-003 (assistant-message JSON pre-fill)

## Context

ADR-003 used Anthropic's assistant-message pre-fill technique to force
JSON-only responses: the conversation ended with `assistant: "{"` so the
model continued from an open brace and could not wrap the response in
prose. This worked on Claude 3.x but Claude 4-series models refuse the
request — the API returns 400 with *"This model does not support assistant
message prefill. The conversation must end with a user message."*

## Decision

Replace the pre-fill technique with **Anthropic tool_use** plus forced
`tool_choice`:

1. The agent declares a single tool (`submit_diagnostic_result`) whose
   `input_schema` mirrors the relevant subset of `report-schema.json`
   (hypotheses array with rank/description/confidence/evidence/
   suggested_pattern_id/reasoning).
2. `messages.create(...)` is called with `tools=[DIAGNOSTIC_TOOL]` and
   `tool_choice={"type": "tool", "name": "submit_diagnostic_result"}`,
   which constrains the model to call exactly that tool.
3. The user message is now the *last* turn in the conversation — no
   assistant pre-fill.
4. The wrapper extracts the `tool_use` block from `resp.content`, returns
   its `.input` dict (and the resolved model identifier from `resp.model`)
   via a small `ToolCallResult` dataclass, and the agent validates the
   dict through pydantic exactly as before.
5. No `temperature` kwarg — Claude 4.x rejects it, and forced tool_use
   already produces deterministic structured output.

Failure modes still raise the same typed exceptions:

* Model returns content with no matching `tool_use` block → client raises
  `ValueError`; agent wraps as `DiagnosticParseError("non-JSON response: …")`.
* Tool input fails pydantic validation → `DiagnosticParseError`.
* Evidence array misses every `notable_findings` entry → retry once with
  feedback in the prompt, then `EvidenceMissingError`.

## Trade-offs

* **Stronger schema enforcement.** The `input_schema` rejects malformed
  shapes at the API layer before the response hits the wire, so we burn
  fewer tokens on obviously-bad responses.
* **No tool_use → no response.** Forced `tool_choice` means a model that
  *can't* answer (e.g. transient API issue) returns content with a
  non-tool block, which we surface as `DiagnosticParseError` rather than
  silently accepting an empty result.
* **One extra hop in the test fakes.** Mocks now return either a `dict`
  (auto-wrapped) or a `ToolCallResult`; the integration-test fake had to
  be updated. Worth it for the correctness guarantees.
* **Provenance is preserved.** `DiagnosticResult.model_version` is now
  filled from `resp.model` (e.g. a date-pinned alias like
  `claude-opus-4-7-20260118`) instead of the literal string `"unknown"`,
  so reports record what the API actually served.

## Why pre-fill is the wrong tool here

Pre-fill optimised for "force a JSON shape onto a model that has no
native structured-output channel." Anthropic now ships exactly that
channel — `tools` + `tool_choice` — and Claude 4 makes it mandatory.
Keeping pre-fill would have meant either pinning to 3.x indefinitely or
maintaining two code paths.
