"""Integration test for the P0420 catalyst-threshold demo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from diagforge import cli as cli_module
from diagforge.diagnostic.agent import ToolCallResult
from diagforge.report.models import Report

pytestmark = pytest.mark.integration

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "p0420_catalyst_threshold"


class _FakeAnthropicClient:
    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:
        marker = '"o2_voltage_b1s2 spiked'
        if marker in user:
            finding = user[user.index(marker) : user.index('"', user.index(marker) + 1)]
            finding = finding.strip('"')
        else:
            finding = "o2_voltage_b1s2 spiked 8 time(s)"
        tool_input = {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "Catalyst monitor latches on every rich excursion",
                    "confidence": "high",
                    "evidence": [finding],
                    "suggested_pattern_id": "dematuration_timer",
                    "reasoning": (
                        "Periodic post-cat spikes at ~300ms spacing without a "
                        "dematuration timer cause set/clear chatter."
                    ),
                }
            ]
        }
        return ToolCallResult(input=tool_input, resolved_model=f"{model}-mocked")


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "RealAnthropicClient", lambda *a, **kw: _FakeAnthropicClient())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_p0420_demo_end_to_end(tmp_path: Path, fake_client: None) -> None:
    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        cli_module.main,
        [
            "analyze",
            str(EXAMPLE_DIR / "trace.asc"),
            "--dtcs",
            str(EXAMPLE_DIR / "dtcs.json"),
            "--dbc",
            str(EXAMPLE_DIR / "o2.dbc"),
            "--output",
            str(out_dir),
            # Catalyst monitoring needs a wider window than the default 500 ms.
            "--window-ms",
            "1500",
        ],
    )
    assert result.exit_code == 0, result.output
    report = Report.model_validate_json((out_dir / "report.json").read_text())
    assert len(report.analyses) == 1

    analysis = report.analyses[0]
    assert analysis.dtc.code == "P0420"
    spike_count = sum(
        1 for a in analysis.pattern_features.transition_anomalies if a.anomaly_type == "value_spike"
    )
    assert spike_count == 8

    matches = analysis.mitigation_matches
    assert len(matches) == 1
    assert matches[0].pattern_id == "dematuration_timer"

    # dematuration_time_ms ≈ 5 × median inter-spike period (~300 ms) rounded to 50 ms boundary.
    dem = next(p for p in matches[0].parameter_suggestions if p.name == "dematuration_time_ms")
    assert isinstance(dem.suggested_value, int | float)
    assert 1200 <= dem.suggested_value <= 1800
    assert "oscillation period" in dem.rationale
