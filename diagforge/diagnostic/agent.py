"""Diagnostic agent — calls Claude, validates, retries on missing-evidence.

The agent is the single LLM-facing component. Every external call is mediated
by `AnthropicClient` so tests can substitute a deterministic fake. On any
failure path the raw model response is logged (truncated) and a typed
exception is raised — silent dropping of API output is explicitly forbidden
in CLAUDE.md.
"""

from __future__ import annotations

import json
from typing import Protocol

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

DEFAULT_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-opus-4-7"
MAX_RESPONSE_TOKENS = 2048


class DiagnosticError(Exception):
    """Base class for all diagnostic-agent failures."""


class DiagnosticParseError(DiagnosticError):
    """Model response could not be parsed into the expected pydantic schema."""


class EvidenceMissingError(DiagnosticError):
    """Model returned hypotheses that did not cite notable_findings verbatim."""


class AnthropicClient(Protocol):
    """Minimal seam between DiagForge and the Anthropic SDK.

    A concrete implementation calls `messages.create`; the test fake returns
    canned JSON without any network traffic.
    """

    def create_message(
        self, *, system: str, user: str, model: str, max_tokens: int
    ) -> str:  # pragma: no cover - protocol
        ...


class RealAnthropicClient:
    """Thin wrapper over `anthropic.Anthropic` that returns the raw assistant text."""

    def __init__(self, api_key: str | None = None) -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key) if api_key else Anthropic()

    def create_message(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                {"role": "user", "content": user},
                {"role": "assistant", "content": "{"},
            ],
            temperature=0.0,
        )
        # Pre-fill of "{" pushes the model into JSON immediately; rejoin it here.
        text_parts: list[str] = []
        for block in resp.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        body = "".join(text_parts)
        # Strip any markdown fence the model may still emit despite the system prompt.
        body = body.strip()
        if body.startswith("```"):
            body = body.split("\n", 1)[1] if "\n" in body else body[3:]
        if body.endswith("```"):
            body = body.rsplit("```", 1)[0]
        return "{" + body if not body.lstrip().startswith("{") else body


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"...<truncated {len(s) - n} more chars>"


class DiagnosticAgent:
    """Build a prompt → call the model → parse → validate evidence → maybe retry."""

    def __init__(
        self,
        client: AnthropicClient,
        model: str = DEFAULT_MODEL,
        model_version: str = "unknown",
        max_response_tokens: int = MAX_RESPONSE_TOKENS,
    ) -> None:
        self._client = client
        self.model = model
        self.model_version = model_version
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
        raw = self._client.create_message(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            model=self.model,
            max_tokens=self.max_response_tokens,
        )
        return self._parse(raw, features, user_prompt)

    def _parse(self, raw: str, features: PatternFeatures, user_prompt: str) -> DiagnosticResult:
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.error("model returned non-JSON response: %s", _truncate(raw))
            raise DiagnosticParseError(f"non-JSON response: {exc}") from exc

        if not isinstance(blob, dict) or "hypotheses" not in blob:
            _log.error("model response missing 'hypotheses': %s", _truncate(raw))
            raise DiagnosticParseError("response missing top-level 'hypotheses' array")

        try:
            hypotheses = [Hypothesis.model_validate(h) for h in blob["hypotheses"]]
        except ValidationError as exc:
            _log.error("hypotheses failed validation: %s\nraw=%s", exc, _truncate(raw))
            raise DiagnosticParseError(f"hypothesis validation failed: {exc}") from exc

        if not hypotheses:
            _log.error("model returned zero hypotheses: %s", _truncate(raw))
            raise DiagnosticParseError("zero hypotheses returned")

        return DiagnosticResult(
            hypotheses=hypotheses,
            model=self.model,
            model_version=self.model_version,
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
