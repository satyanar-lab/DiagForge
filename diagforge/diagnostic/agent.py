"""Diagnostic agent — calls Claude via tool_use, validates, retries on missing evidence.

Claude 4-series models do not support assistant-message prefill; instead, we
force structured output by declaring a single Anthropic `tool` whose JSON
schema matches the diagnostic-result shape and constraining the model to
call it via `tool_choice`. The model's tool input is returned as a dict,
parsed and validated through pydantic exactly as before.

Every external call goes through `AnthropicClient` so tests can substitute a
deterministic fake. On any failure path the raw response is logged
(truncated) and a typed exception is raised — silent dropping of API output
is explicitly forbidden in CLAUDE.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Protocol

from pydantic import ValidationError

from diagforge._logging import get_logger
from diagforge.diagnostic.prompts import (
    PROMPT_TEMPLATE_VERSION,
    SYSTEM_PROMPT,
    build_diagnostic_prompt,
    prompt_hash,
)
from diagforge.ingestion.models import DTCSnapshot
from diagforge.report.models import DiagnosticResult, Hypothesis, PatternFeatures

_log = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-4-7"
FALLBACK_MODEL = "claude-sonnet-4-6"
MAX_RESPONSE_TOKENS = 2048


@dataclass(frozen=True)
class ToolCallResult:
    """What the diagnostic client returns from a forced tool_use call.

    `input` is the validated dict the model passed to the tool.
    `resolved_model` is the model identifier the API actually used to serve
    the request (e.g. a pinned date-suffixed alias) — falls back to the
    requested model name if the SDK doesn't surface a resolved one.
    """

    input: dict[str, Any]
    resolved_model: str


#: Tool name the model is forced to call. Stable identifier — bump if the
#: input_schema changes in a way that would shift model behavior.
DIAGNOSTIC_TOOL_NAME: Final[str] = "submit_diagnostic_result"

#: Anthropic tool definition. The input_schema mirrors the relevant subset of
#: report-schema.json (Hypothesis fields). Model fields (model name, version,
#: prompt template version, prompt hash) are stamped on by the agent itself
#: after the tool fires, since the model has no business asserting them.
DIAGNOSTIC_TOOL: Final[dict[str, Any]] = {
    "name": DIAGNOSTIC_TOOL_NAME,
    "description": (
        "Submit ranked root-cause hypotheses for the DTC under investigation. "
        "Each hypothesis MUST cite at least one string from notable_findings "
        "verbatim in its `evidence` array."
    ),
    "input_schema": {
        "type": "object",
        "required": ["hypotheses"],
        "additionalProperties": False,
        "properties": {
            "hypotheses": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": [
                        "rank",
                        "description",
                        "confidence",
                        "evidence",
                        "reasoning",
                    ],
                    "additionalProperties": False,
                    "properties": {
                        "rank": {"type": "integer", "minimum": 1},
                        "description": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                        },
                        "suggested_pattern_id": {"type": ["string", "null"]},
                        "reasoning": {"type": "string"},
                    },
                },
            }
        },
    },
}


class DiagnosticError(Exception):
    """Base class for all diagnostic-agent failures."""


class DiagnosticParseError(DiagnosticError):
    """Model response could not be parsed into the expected pydantic schema."""


class EvidenceMissingError(DiagnosticError):
    """Model returned hypotheses that did not cite notable_findings verbatim."""


class AnthropicClient(Protocol):
    """Minimal seam between DiagForge and the Anthropic SDK.

    A concrete implementation forces a single tool call via `tool_choice` and
    returns a `ToolCallResult` carrying the tool input dict and the resolved
    model identifier. The test fake returns canned values without any
    network traffic.
    """

    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:  # pragma: no cover - protocol
        ...


class RealAnthropicClient:
    """Thin wrapper over `anthropic.Anthropic` that returns a forced tool_use input."""

    def __init__(self, api_key: str | None = None) -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key) if api_key else Anthropic()

    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:
        # The SDK's `tools` / `tool_choice` parameters are typed as a sealed
        # union of TypedDicts whose shape matches what we build dynamically.
        # mypy can't bridge the dict[str, Any] → TypedDict gap, so suppress
        # the overload check on this one call.
        # Note: no `temperature` kwarg — Claude 4.x rejects it; forced tool_use
        # already produces deterministic structured output.
        resp = self._client.messages.create(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
        # The API returns the actual resolved model in `resp.model` (e.g. a
        # date-pinned alias). Fall back to the requested model if the SDK
        # doesn't surface one.
        resolved = str(getattr(resp, "model", "") or model)
        for block in resp.content:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == tool["name"]
            ):
                tool_input = getattr(block, "input", None)
                if not isinstance(tool_input, dict):
                    raise ValueError(
                        f"tool_use block had non-dict input: {type(tool_input).__name__}"
                    )
                return ToolCallResult(input=dict(tool_input), resolved_model=resolved)
        observed = [getattr(b, "type", "?") for b in resp.content]
        raise ValueError(
            f"model did not call tool {tool['name']!r}; got content block types {observed}"
        )


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"...<truncated {len(s) - n} more chars>"


class DiagnosticAgent:
    """Build a prompt → call the model via tool_use → validate → maybe retry."""

    def __init__(
        self,
        client: AnthropicClient,
        model: str = DEFAULT_MODEL,
        max_response_tokens: int = MAX_RESPONSE_TOKENS,
    ) -> None:
        self._client = client
        self.model = model
        self.max_response_tokens = max_response_tokens

    def propose(
        self,
        dtc: DTCSnapshot,
        features: PatternFeatures,
        available_pattern_ids: list[str],
    ) -> DiagnosticResult:
        user_prompt = build_diagnostic_prompt(dtc, features, available_pattern_ids)
        result = self._one_shot(features, user_prompt)
        missing = self._missing_evidence(result, features)
        if missing:
            feedback = self._format_missing_feedback(missing, features)
            _log.warning(
                "diagnostic agent missed verbatim evidence on first attempt; retrying once: %s",
                feedback,
            )
            retry_prompt = build_diagnostic_prompt(
                dtc, features, available_pattern_ids, feedback=feedback
            )
            result = self._one_shot(features, retry_prompt)
            missing = self._missing_evidence(result, features)
            if missing:
                raise EvidenceMissingError(
                    f"Retry exhausted; hypotheses still miss verbatim findings: {missing}"
                )
        return result

    def _one_shot(self, features: PatternFeatures, user_prompt: str) -> DiagnosticResult:
        try:
            result = self._client.call_with_tool(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                model=self.model,
                max_tokens=self.max_response_tokens,
                tool=DIAGNOSTIC_TOOL,
            )
        except ValueError as exc:
            # Client refused the tool call or returned an unusable shape.
            _log.error("client refused tool call: %s", exc)
            raise DiagnosticParseError(f"non-JSON response: {exc}") from exc
        return self._parse(result, features, user_prompt)

    def _parse(
        self,
        result: ToolCallResult,
        features: PatternFeatures,
        user_prompt: str,
    ) -> DiagnosticResult:
        # features is reserved for hypothesis-level cross-checks in Phase 0.
        _ = features
        raw = result.input
        if not isinstance(raw, dict) or "hypotheses" not in raw:
            _log.error("tool input missing 'hypotheses': %s", _truncate(json.dumps(raw)))
            raise DiagnosticParseError("response missing top-level 'hypotheses' array")

        try:
            hypotheses = [Hypothesis.model_validate(h) for h in raw["hypotheses"]]
        except ValidationError as exc:
            _log.error(
                "hypotheses failed validation: %s\nraw=%s",
                exc,
                _truncate(json.dumps(raw)),
            )
            raise DiagnosticParseError(f"hypothesis validation failed: {exc}") from exc

        if not hypotheses:
            _log.error("model returned zero hypotheses: %s", _truncate(json.dumps(raw)))
            raise DiagnosticParseError("zero hypotheses returned")

        return DiagnosticResult(
            hypotheses=hypotheses,
            model=self.model,
            model_version=result.resolved_model,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            prompt_hash=prompt_hash(SYSTEM_PROMPT, user_prompt),
        )

    @staticmethod
    def _missing_evidence(
        result: DiagnosticResult, features: PatternFeatures
    ) -> list[tuple[int, list[str]]]:
        """Return (rank, evidence_list) pairs whose evidence does not match notable_findings."""
        findings = set(features.notable_findings)
        misses: list[tuple[int, list[str]]] = []
        for h in result.hypotheses:
            if not h.evidence or not any(ev in findings for ev in h.evidence):
                misses.append((h.rank, list(h.evidence)))
        return misses

    @staticmethod
    def _format_missing_feedback(
        misses: list[tuple[int, list[str]]], features: PatternFeatures
    ) -> str:
        finding_lines = "\n".join(f'  - "{f}"' for f in features.notable_findings)
        miss_lines = "\n".join(f"  hypothesis rank {rank}: evidence={ev}" for rank, ev in misses)
        return (
            "Some hypotheses did not cite notable_findings verbatim.\n"
            "OFFENDING HYPOTHESES:\n"
            f"{miss_lines}\n"
            "VALID NOTABLE FINDINGS (you must quote one of these exactly):\n"
            f"{finding_lines}"
        )
