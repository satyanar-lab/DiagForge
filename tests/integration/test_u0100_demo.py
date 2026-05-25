"""Integration test for the U0100 lost-communication demo.

Mirrors the P0300 test shape: mocks Anthropic, runs the CLI, validates the
emitted JSON bundle for the expected analyzer findings and the
communication_retry_state_machine mitigation match with computed
timeout_ms / clear_holdoff_ms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from diagforge import cli as cli_module
from diagforge.diagnostic.agent import ToolCallResult
from diagforge.report.models import Report

pytestmark = pytest.mark.integration

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "u0100_lost_comm"


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:
        self.calls.append({"system": system, "user": user, "model": model})
        # Pull the verbatim finding out of the prompt so the citation matches.
        marker = '"engine_temp had'
        if marker in user:
            finding = user[user.index(marker) : user.index('"', user.index(marker) + 1)]
            finding = finding.strip('"')
        else:
            finding = "engine_temp had 4 communication gap(s)"
        tool_input = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "Single-shot lost-comm threshold; no retry counter",
                    "confidence": "high",
                    "evidence": [finding],
                    "suggested_pattern_id": "communication_retry_state_machine",
                    "reasoning": (
                        "Multiple ECM publish gaps of ~250-400ms each set U0100 "
                        "even though wheel_speed kept publishing — bus is fine, the "
                        "monitor needs a consecutive-misses counter."
                    ),
                }
            ]
        }
        return ToolCallResult(input=tool_input, resolved_model=f"{model}-mocked")


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch) -> _FakeAnthropicClient:
    fake = _FakeAnthropicClient()
    monkeypatch.setattr(cli_module, "RealAnthropicClient", lambda *a, **kw: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return fake


def test_u0100_demo_pipeline_end_to_end(
    tmp_path: Path, fake_client_factory: _FakeAnthropicClient
) -> None:
    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        cli_module.main,
        [
            "analyze",
            str(EXAMPLE_DIR / "trace.asc"),
            "--dtcs",
            str(EXAMPLE_DIR / "dtcs.json"),
            "--dbc",
            str(EXAMPLE_DIR / "engine_bus.dbc"),
            "--output",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    report = Report.model_validate_json((out_dir / "report.json").read_text())
    assert len(report.analyses) == 1

    analysis = report.analyses[0]
    assert analysis.dtc.code == "U0100"
    assert any(
        a.anomaly_type == "communication_gap"
        for a in analysis.pattern_features.transition_anomalies
    )
    assert any("communication gap" in f for f in analysis.pattern_features.notable_findings)

    matches = analysis.mitigation_matches
    assert len(matches) == 1
    assert matches[0].pattern_id == "communication_retry_state_machine"

    # Computed parameters: timeout_ms ≈ 30 (3 × 10 ms publish interval).
    tm = next(p for p in matches[0].parameter_suggestions if p.name == "timeout_ms")
    assert tm.suggested_value == 30
    holdoff = next(p for p in matches[0].parameter_suggestions if p.name == "clear_holdoff_ms")
    assert holdoff.suggested_value == 200
