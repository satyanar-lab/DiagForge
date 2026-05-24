"""Abstract parser interfaces for Layer 1 ingestion.

Every concrete parser (ASC, BLF, UDS, DTC-JSON) implements one of these,
which lets the CLI dispatch on file extension without knowing the concrete
class. Parsers must be deterministic: same bytes in → same events out.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from diagforge.ingestion.models import DTCSnapshot, TraceEvent


class IngestionError(Exception):
    """Raised when an input file is unreadable, malformed, or violates the format spec."""


class TraceParser(ABC):
    """Parse a CAN/UDS log into a list of normalized TraceEvent."""

    #: Lowercase file extensions this parser accepts, e.g. (".asc", ".log").
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, path: Path) -> list[TraceEvent]:  # pragma: no cover - interface
        ...

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.extensions


class DtcParser(ABC):
    """Parse a DTC snapshot file into a list of DTCSnapshot."""

    extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, path: Path) -> list[DTCSnapshot]:  # pragma: no cover - interface
        ...

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.extensions
