"""Trace-parser registry — dispatches by file extension.

Adding a new format means appending the parser class to `_PARSERS`. The CLI
and UI both call `parser_for(path)` rather than hard-coding extensions,
which is what kept the Phase 0-Lite CLI welded to `.asc` and forced a
refactor when UDS .log arrived.
"""

from __future__ import annotations

from pathlib import Path

from diagforge.ingestion.base import IngestionError, TraceParser
from diagforge.ingestion.can_asc import AscTraceParser
from diagforge.ingestion.uds import UdsLogParser

_PARSERS: list[type[TraceParser]] = [AscTraceParser, UdsLogParser]


def parser_for(path: Path) -> TraceParser:
    """Return a fresh parser instance for the given path's extension."""
    for cls in _PARSERS:
        if cls.can_parse(path):
            return cls()
    accepted = ", ".join(sorted({ext for cls in _PARSERS for ext in cls.extensions}))
    raise IngestionError(
        f"no trace parser registered for extension {path.suffix!r}; accepted: {accepted}"
    )


def supported_extensions() -> list[str]:
    """Lowercase extensions accepted by at least one registered parser."""
    return sorted({ext for cls in _PARSERS for ext in cls.extensions})


def format_for(path: Path) -> str:
    """Return the canonical format tag (asc / log / blf / csv) for the report."""
    return path.suffix.lower().lstrip(".")
