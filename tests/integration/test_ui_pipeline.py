"""Integration test for the UI's library-mode pipeline.

The Streamlit module itself isn't loaded (it requires `streamlit run`),
but `run_pipeline` is the same code path the UI hits after file upload,
and is what proves the UI can analyse without going through the CLI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from diagforge.diagnostic.agent import ToolCallResult
from diagforge.ui.pipeline import run_pipeline

pytestmark = pytest.mark.integration

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "p0300_intermittent_misfire"


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.calls = 0

    def call_with_tool(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        tool: dict[str, Any],
    ) -> ToolCallResult:
        self.calls += 1
        # Find any quoted "<signal> dropped N time(s)" finding in the prompt
        # — works whether the analyzer saw `engine_rpm` (with DBC) or
        # `frame_0x100` (auto-decoder).
        finding = "no finding extracted"
        import re

        match = re.search(r'"([^"]*\bdropped\b[^"]*)"', user)
        if match:
            finding = match.group(1)
        return ToolCallResult(
            input={
                "hypotheses": [
                    {
                        "rank": 1,
                        "description": "Insufficient misfire dematuration",
                        "confidence": "high",
                        "evidence": [finding],
                        "suggested_pattern_id": "duration_qualified_debounce",
                        "reasoning": "Short repeated RPM sags pass a single-shot threshold.",
                    }
                ]
            },
            resolved_model=f"{model}-mocked",
        )


def test_run_pipeline_end_to_end(tmp_path: Path) -> None:
    progress_log: list[str] = []
    result = run_pipeline(
        trace_path=EXAMPLE_DIR / "trace.asc",
        dtcs_path=EXAMPLE_DIR / "dtcs.json",
        dbc_path=EXAMPLE_DIR / "engine.dbc",
        out_dir=tmp_path / "out",
        model="claude-opus-4-7",
        window_ms=500,
        client=_FakeAnthropicClient(),
        progress=progress_log.append,
    )

    # The pipeline result carries everything the Streamlit UI needs.
    assert len(result.report.analyses) == 1
    assert result.report.analyses[0].dtc.code == "P0300"
    assert len(result.charts) == 1
    assert result.charts[0].startswith("<svg")

    # Files written and reloaded.
    assert (tmp_path / "out" / "report.json").exists()
    assert (tmp_path / "out" / "report.html").exists()
    assert (tmp_path / "out" / "manifest.json").exists()
    assert result.report_json_bytes
    assert result.report_html_bytes
    assert result.manifest_json_bytes
    assert result.bundle_zip_bytes
    # Zip is a real zip (PK header).
    assert result.bundle_zip_bytes[:2] == b"PK"

    # Progress callback fired with sensible step messages.
    joined = "\n".join(progress_log)
    assert "Ingesting trace" in joined
    assert "Analyzing DTC" in joined
    assert "Done" in joined


def test_run_pipeline_without_dbc_uses_auto_decoder(tmp_path: Path) -> None:
    result = run_pipeline(
        trace_path=EXAMPLE_DIR / "trace.asc",
        dtcs_path=EXAMPLE_DIR / "dtcs.json",
        dbc_path=None,
        out_dir=tmp_path / "out",
        model="claude-opus-4-7",
        window_ms=500,
        client=_FakeAnthropicClient(),
    )
    # Without a DBC the analyzer sees `frame_0x100` instead of `engine_rpm`,
    # but the pipeline still completes and produces a report.
    assert len(result.report.analyses) == 1
    assert result.report.input.dbc_file is None
