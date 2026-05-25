"""Unit tests for the diagnostic agent. The Anthropic client is mocked.

After the refactor to tool_use, the client returns a dict (the tool input)
instead of a JSON string. The "non-JSON" failure mode is replaced by the
"client refused the tool call" path (the client raises ValueError, which the
agent wraps as DiagnosticParseError).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from diagforge.diagnostic.agent import (
    DEFAULT_MODEL,
    DIAGNOSTIC_TOOL,
    DIAGNOSTIC_TOOL_NAME,
    DiagnosticAgent,
    DiagnosticParseError,
    EvidenceMissingError,
    ToolCallResult,
)
from diagforge.diagnostic.prompts import PROMPT_TEMPLATE_VERSION
from diagforge.ingestion.models import DTCSnapshot
from diagforge.report.models import PatternFeatures

FINDING_A = (
    "engine_rpm dropped 4 time(s) within 500ms of the DTC window — "
    "extreme values [50, 70, 90, 130], durations (ms) [40, 40, 40, 40]"
)
FINDING_B = "engine_rpm bounce burst: bouncy debounce"


def _features() -> PatternFeatures:
    return PatternFeatures(
        window_us=500_000,
        signal_summaries=[],
        transition_anomalies=[],
        correlations=[],
        notable_findings=[FINDING_A, FINDING_B],
    )


def _dtc() -> DTCSnapshot:
    return DTCSnapshot(
        dtc_code="P0300",
        standard="obd2",
        timestamp_first_us=480_000,
        timestamp_latest_us=690_000,
        occurrence_count=1,
    )


class _CannedClient:
    """Test double — returns a sequence of canned tool_use results.

    Each item is one of:
    * a dict           — wrapped as a `ToolCallResult` whose `resolved_model`
                         mirrors the requested model.
    * a ToolCallResult — returned directly.
    * an Exception     — raised when the client is called.
    """

    def __init__(self, responses: list[dict[str, Any] | ToolCallResult | Exception]) -> None:
        self._responses = list(responses)
        self.call_log: list[dict[str, Any]] = []

    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:
        self.call_log.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "max_tokens": max_tokens,
                "tool": tool,
            }
        )
        if not self._responses:
            raise AssertionError("client called more times than responses provided")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ToolCallResult):
            return item
        return ToolCallResult(input=item, resolved_model=model)


def _good_response_citing(finding: str) -> dict[str, Any]:
    return {
        "hypotheses": [
            {
                "rank": 1,
                "description": "intermittent rpm dropout",
                "confidence": "medium",
                "evidence": [finding],
                "suggested_pattern_id": "dematuration_timer",
                "reasoning": "Brief drops near idle threshold suggest a missing dematuration timer.",
            }
        ]
    }


class TestHappyPath:
    def test_returns_diagnostic_result(self) -> None:
        client = _CannedClient([_good_response_citing(FINDING_A)])
        agent = DiagnosticAgent(client, model="claude-opus-4-7")
        out = agent.propose(_dtc(), _features(), ["dematuration_timer"])
        assert out.model == "claude-opus-4-7"
        assert out.prompt_template_version == PROMPT_TEMPLATE_VERSION
        assert out.hypotheses[0].suggested_pattern_id == "dematuration_timer"
        assert len(client.call_log) == 1
        # the tool definition is passed through on every call
        assert client.call_log[0]["tool"]["name"] == DIAGNOSTIC_TOOL_NAME

    def test_default_model_is_opus_4_7(self) -> None:
        assert DEFAULT_MODEL == "claude-opus-4-7"
        client = _CannedClient([_good_response_citing(FINDING_A)])
        # Agent constructed without explicit model uses DEFAULT_MODEL.
        agent = DiagnosticAgent(client)
        out = agent.propose(_dtc(), _features(), [])
        assert out.model == "claude-opus-4-7"

    def test_model_version_mirrors_resolved_model_from_api(self) -> None:
        client = _CannedClient(
            [
                ToolCallResult(
                    input=_good_response_citing(FINDING_A),
                    resolved_model="claude-opus-4-7-20260118",
                )
            ]
        )
        agent = DiagnosticAgent(client, model="claude-opus-4-7")
        out = agent.propose(_dtc(), _features(), [])
        # The API's resolved alias is preserved verbatim in model_version.
        assert out.model_version == "claude-opus-4-7-20260118"
        assert out.model == "claude-opus-4-7"

    def test_model_version_falls_back_to_requested_when_api_silent(self) -> None:
        """If the API doesn't surface a resolved model, mirror the request."""
        client = _CannedClient([_good_response_citing(FINDING_A)])
        agent = DiagnosticAgent(client, model="claude-opus-4-7")
        out = agent.propose(_dtc(), _features(), [])
        assert out.model_version == "claude-opus-4-7"

    def test_tool_definition_constrains_confidence_enum(self) -> None:
        schema = DIAGNOSTIC_TOOL["input_schema"]
        item = schema["properties"]["hypotheses"]["items"]
        assert item["properties"]["confidence"]["enum"] == ["low", "medium", "high"]


class TestParseFailures:
    def test_tool_not_called_raises_parse_error(self) -> None:
        """When the model returns content with no matching tool_use block."""
        client = _CannedClient([ValueError("model did not call tool 'submit_diagnostic_result'")])
        agent = DiagnosticAgent(client)
        with pytest.raises(DiagnosticParseError, match="non-JSON"):
            agent.propose(_dtc(), _features(), [])

    def test_missing_hypotheses_key_raises(self) -> None:
        client = _CannedClient([{"results": []}])
        agent = DiagnosticAgent(client)
        with pytest.raises(DiagnosticParseError, match="missing top-level"):
            agent.propose(_dtc(), _features(), [])

    def test_empty_hypotheses_raises(self) -> None:
        client = _CannedClient([{"hypotheses": []}])
        agent = DiagnosticAgent(client)
        with pytest.raises(DiagnosticParseError, match="zero hypotheses"):
            agent.propose(_dtc(), _features(), [])

    def test_hypothesis_with_bad_confidence_raises(self) -> None:
        bad = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "x",
                    "confidence": "definitely",
                    "evidence": [FINDING_A],
                    "suggested_pattern_id": None,
                    "reasoning": "y",
                }
            ]
        }
        client = _CannedClient([bad])
        agent = DiagnosticAgent(client)
        with pytest.raises(DiagnosticParseError, match="validation"):
            agent.propose(_dtc(), _features(), [])


class TestMissingEvidenceRetry:
    def test_retry_succeeds_on_second_attempt(self) -> None:
        bad = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "x",
                    "confidence": "low",
                    "evidence": ["I just made this up"],
                    "suggested_pattern_id": None,
                    "reasoning": "guessing",
                }
            ]
        }
        client = _CannedClient([bad, _good_response_citing(FINDING_A)])
        agent = DiagnosticAgent(client)
        out = agent.propose(_dtc(), _features(), [])
        assert len(client.call_log) == 2
        # the retry prompt must contain the offending evidence as feedback
        assert "FEEDBACK FROM LAST ATTEMPT" in str(client.call_log[1]["user"])
        assert out.hypotheses[0].evidence == [FINDING_A]

    def test_retry_failure_raises_evidence_missing(self) -> None:
        bad = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "x",
                    "confidence": "low",
                    "evidence": ["fabricated"],
                    "suggested_pattern_id": None,
                    "reasoning": "z",
                }
            ]
        }
        client = _CannedClient([bad, bad])
        agent = DiagnosticAgent(client)
        with pytest.raises(EvidenceMissingError, match="Retry exhausted"):
            agent.propose(_dtc(), _features(), [])
        assert len(client.call_log) == 2

    def test_only_one_retry_on_evidence_miss(self) -> None:
        """Confirms we don't loop on persistent failures."""
        bad = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "x",
                    "confidence": "low",
                    "evidence": ["fabricated"],
                    "suggested_pattern_id": None,
                    "reasoning": "z",
                }
            ]
        }

        calls = 0

        def respond(
            *,
            system: str,
            user: str,
            model: str,
            max_tokens: int,
            tool: dict[str, Any],
        ) -> ToolCallResult:
            nonlocal calls
            calls += 1
            return ToolCallResult(input=bad, resolved_model=model)

        class _C:
            call_with_tool: Callable[..., ToolCallResult] = staticmethod(respond)

        agent = DiagnosticAgent(_C())
        with pytest.raises(EvidenceMissingError):
            agent.propose(_dtc(), _features(), [])
        assert calls == 2


class TestTimeoutPropagation:
    def test_client_exception_propagates(self) -> None:
        client = _CannedClient([TimeoutError("API timeout")])
        agent = DiagnosticAgent(client)
        with pytest.raises(TimeoutError, match="API timeout"):
            agent.propose(_dtc(), _features(), [])


class TestPatternIdAcceptance:
    def test_unknown_pattern_id_passes_through(self) -> None:
        """The agent does not validate suggested_pattern_id — the recommender does."""
        resp = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "x",
                    "confidence": "low",
                    "evidence": [FINDING_A],
                    "suggested_pattern_id": "this_pattern_does_not_exist",
                    "reasoning": "still valid output",
                }
            ]
        }
        client = _CannedClient([resp])
        agent = DiagnosticAgent(client)
        out = agent.propose(_dtc(), _features(), ["dematuration_timer"])
        assert out.hypotheses[0].suggested_pattern_id == "this_pattern_does_not_exist"


class TestPrompt:
    def test_prompt_includes_dtc_and_findings(self) -> None:
        client = _CannedClient([_good_response_citing(FINDING_A)])
        agent = DiagnosticAgent(client)
        agent.propose(_dtc(), _features(), ["dematuration_timer"])
        user = client.call_log[0]["user"]
        assert isinstance(user, str)
        assert "P0300" in user
        assert FINDING_A in user
        assert "dematuration_timer" in user
        # The prompt now instructs the model to call the tool by name.
        assert DIAGNOSTIC_TOOL_NAME in user
