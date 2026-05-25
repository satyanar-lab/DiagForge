"""End-to-end test: multi-DTC trace produces cross_dtc_findings in the report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from diagforge import cli as cli_module
from diagforge.diagnostic.agent import ToolCallResult
from diagforge.report.models import Report

pytestmark = pytest.mark.integration

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "p0300_intermittent_misfire"


class _FakeClient:
    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:
        import re

        match = re.search(r'"([^"]*\b(?:dropped|spiked|silent|gap)\b[^"]*)"', user)
        finding = match.group(1) if match else "synthetic"
        return ToolCallResult(
            input={
                "hypotheses": [
                    {
                        "rank": 1,
                        "description": "synthetic",
                        "confidence": "medium",
                        "evidence": [finding],
                        "suggested_pattern_id": None,
                        "reasoning": "test",
                    }
                ]
            },
            resolved_model=f"{model}-mocked",
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "RealAnthropicClient", lambda *a, **kw: _FakeClient())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_co_occurring_dtcs_produce_cross_dtc_finding(tmp_path: Path, fake: None) -> None:
    # Reuse the existing P0300 trace; build a custom dtcs.json with two
    # DTCs whose first-seen timestamps fall inside the 100ms window.
    dtcs_path = tmp_path / "dtcs.json"
    dtcs_path.write_text(
        json.dumps(
            {
                "dtcs": [
                    {
                        "dtc_code": "P0300",
                        "standard": "obd2",
                        "timestamp_first_us": 510_000,
                        "timestamp_latest_us": 770_000,
                        "occurrence_count": 1,
                    },
                    {
                        "dtc_code": "P0301",
                        "standard": "obd2",
                        "timestamp_first_us": 560_000,
                        "timestamp_latest_us": 760_000,
                        "occurrence_count": 1,
                    },
                ]
            }
        )
    )
    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        cli_module.main,
        [
            "analyze",
            str(EXAMPLE_DIR / "trace.asc"),
            "--dtcs",
            str(dtcs_path),
            "--dbc",
            str(EXAMPLE_DIR / "engine.dbc"),
            "--output",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    report = Report.model_validate_json((out_dir / "report.json").read_text())
    assert len(report.analyses) == 2
    assert len(report.cross_dtc_findings) == 1
    finding = report.cross_dtc_findings[0]
    assert finding.type == "co_occurring"
    assert set(finding.dtc_codes) == {"P0300", "P0301"}
    assert finding.delta_us == 50_000

    # HTML reflects the cross-DTC section.
    html = (out_dir / "report.html").read_text()
    assert "Cross-DTC findings" in html
    assert "P0300" in html and "P0301" in html


def test_single_dtc_run_has_no_cross_findings(tmp_path: Path, fake: None) -> None:
    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        cli_module.main,
        [
            "analyze",
            str(EXAMPLE_DIR / "trace.asc"),
            "--dtcs",
            str(EXAMPLE_DIR / "dtcs.json"),
            "--dbc",
            str(EXAMPLE_DIR / "engine.dbc"),
            "--output",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    report = Report.model_validate_json((out_dir / "report.json").read_text())
    assert report.cross_dtc_findings == []
