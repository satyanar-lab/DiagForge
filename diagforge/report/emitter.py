"""Report emitter — assembles per-DTC analyses into the canonical bundle.

The bundle layout:

    <out>/report.json         the structured report (matches report-schema.json)
    <out>/report.html         Jinja2-rendered human-readable summary
    <out>/manifest.json       file index + sha256 hashes
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Final

from diagforge import __version__
from diagforge._logging import get_logger
from diagforge.report.html import render_report_html
from diagforge.report.models import (
    SCHEMA_VERSION,
    DtcAnalysis,
    InputInfo,
    Report,
    ToolInfo,
)

_log = get_logger(__name__)
_REPORT_JSON: Final[str] = "report.json"
_REPORT_HTML: Final[str] = "report.html"
_MANIFEST_JSON: Final[str] = "manifest.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _uuid7() -> str:
    """Generate a UUIDv7 (time-ordered). We need v7 because the schema demands it."""
    ts_ms = int(dt.datetime.now(tz=dt.UTC).timestamp() * 1000)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    high = (ts_ms & 0xFFFFFFFFFFFF) << 16 | 0x7000 | rand_a
    low = 0x8000_0000_0000_0000 | rand_b
    return f"{high:032x}"[:32] and (
        f"{high >> 64 & 0xFFFFFFFF:08x}-"
        f"{high >> 48 & 0xFFFF:04x}-"
        f"{high >> 32 & 0xFFFF:04x}-"
        f"{low >> 48 & 0xFFFF:04x}-"
        f"{low & 0xFFFFFFFFFFFF:012x}"
    )


class ReportEmitter:
    """Build up an analysis run incrementally, then write the bundle."""

    def __init__(self, out_dir: Path, input_info: InputInfo) -> None:
        self._out_dir = out_dir
        self._input = input_info
        self._analyses: list[DtcAnalysis] = []
        self._report_id = _uuid7()
        self._created_at = dt.datetime.now(tz=dt.UTC)

    @property
    def report_id(self) -> str:
        return self._report_id

    def add_analysis(self, analysis: DtcAnalysis) -> None:
        self._analyses.append(analysis)

    def finalize(self) -> Path:
        """Render the report to disk; return the path to `report.json`."""
        self._out_dir.mkdir(parents=True, exist_ok=True)
        report = Report(
            report_id=self._report_id,
            schema_version=SCHEMA_VERSION,
            created_at=self._created_at,
            tool=ToolInfo(version=__version__),
            input=self._input,
            analyses=self._analyses,
        )

        report_json_path = self._out_dir / _REPORT_JSON
        report_json_path.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        html_path = self._out_dir / _REPORT_HTML
        html_path.write_text(render_report_html(report), encoding="utf-8")

        manifest = {
            "report_id": self._report_id,
            "created_at": self._created_at.isoformat(),
            "files": [
                {
                    "name": _REPORT_JSON,
                    "sha256": _sha256_file(report_json_path),
                    "bytes": report_json_path.stat().st_size,
                },
                {
                    "name": _REPORT_HTML,
                    "sha256": _sha256_file(html_path),
                    "bytes": html_path.stat().st_size,
                },
            ],
        }
        manifest_path = self._out_dir / _MANIFEST_JSON
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        _log.info("wrote report bundle to %s (%d analyses)", self._out_dir, len(self._analyses))
        return report_json_path


def sha256_path(path: Path) -> str:
    """Public hashing helper used by the CLI when building InputInfo."""
    return _sha256_file(path)
