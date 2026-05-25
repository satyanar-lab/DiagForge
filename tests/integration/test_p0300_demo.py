"""End-to-end integration test for the P0300 demo case.

The Anthropic client is mocked — this test does NOT touch the network. It
exercises the full CLI pipeline (ingestion → signal decode → analyzer →
mocked diagnostic agent → mitigation recommender → report emitter) and
asserts the resulting JSON bundle is shaped correctly and validates against
the canonical schema models.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from diagforge import cli as cli_module
from diagforge.report.models import Report

pytestmark = pytest.mark.integration

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "p0300_intermittent_misfire"


class _FakeAnthropicClient:
    """Returns a canned tool_use input that cites the expected finding."""

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
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "model": model, "tool": tool["name"]})
        # Extract the verbatim finding from the prompt so the citation always matches.
        marker = '"engine_rpm dropped 4 time(s)'
        if marker in user:
            finding = user[user.index(marker) : user.index('"', user.index(marker) + 1)]
            finding = finding.strip('"')
        else:
            finding = "engine_rpm dropped 4 time(s)"
        return {
            "hypotheses": [
                {
                    "rank": 1,
                    "description": "Insufficient misfire dematuration timer",
                    "confidence": "high",
                    "evidence": [finding],
                    "suggested_pattern_id": "dematuration_timer",
                    "reasoning": (
                        "Short, repeated RPM sags pass a single-shot misfire "
                        "threshold; a dematuration timer would suppress them."
                    ),
                },
                {
                    "rank": 2,
                    "description": "Fuel pressure transient",
                    "confidence": "low",
                    "evidence": [finding],
                    "suggested_pattern_id": None,
                    "reasoning": "Would need fuel-rail trace to confirm.",
                },
            ]
        }


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch) -> _FakeAnthropicClient:
    fake = _FakeAnthropicClient()
    # Constructor-less factory: the CLI does `client = RealAnthropicClient()`.
    monkeypatch.setattr(cli_module, "RealAnthropicClient", lambda *a, **kw: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return fake


def test_cli_analyze_produces_report(
    tmp_path: Path, fake_client_factory: _FakeAnthropicClient
) -> None:
    out_dir = tmp_path / "report"
    runner = CliRunner()
    result = runner.invoke(
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

    report_json = out_dir / "report.json"
    report_html = out_dir / "report.html"
    manifest = out_dir / "manifest.json"
    assert report_json.exists()
    assert report_html.exists()
    assert manifest.exists()

    # Round-trip the JSON through the canonical model.
    report = Report.model_validate_json(report_json.read_text())
    assert report.schema_version == "1.0.0"
    assert len(report.analyses) == 1

    analysis = report.analyses[0]
    assert analysis.dtc.code == "P0300"
    assert analysis.dtc.standard == "obd2"

    findings = analysis.pattern_features.notable_findings
    assert any("dropped 4 time" in f for f in findings)

    hyps = analysis.diagnostic_result.hypotheses
    assert hyps[0].rank == 1
    assert hyps[0].suggested_pattern_id == "dematuration_timer"
    # evidence must cite verbatim from notable_findings
    assert all(any(ev in findings for ev in h.evidence) for h in hyps)

    matches = analysis.mitigation_matches
    assert len(matches) == 1
    assert matches[0].pattern_id == "dematuration_timer"
    assert matches[0].verification_steps  # non-empty
    assert matches[0].standards_references

    # HTML smoke-checks
    html = report_html.read_text()
    assert "P0300" in html
    assert "dematuration_timer" in html
    assert "dropped 4 time" in html

    # the mock was called exactly once (no retry, evidence matched first try)
    assert len(fake_client_factory.calls) == 1


def test_cli_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        [
            "analyze",
            str(EXAMPLE_DIR / "trace.asc"),
            "--dtcs",
            str(EXAMPLE_DIR / "dtcs.json"),
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in (result.output + (result.stderr if result.stderr else ""))


def test_cli_rejects_non_asc_trace(
    tmp_path: Path, fake_client_factory: _FakeAnthropicClient
) -> None:
    fake_trace = tmp_path / "trace.blf"
    fake_trace.write_bytes(b"binary garbage")
    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        [
            "analyze",
            str(fake_trace),
            "--dtcs",
            str(EXAMPLE_DIR / "dtcs.json"),
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 2
