"""ASCII CAN log parser, built on python-can's ASCReader.

ASC is the Vector tool-family ASCII format. python-can handles the messy
header/footer detection; we wrap it so the rest of DiagForge sees a pure
list[TraceEvent] with no external dependency leaking through.
"""

from __future__ import annotations

from pathlib import Path

import can

from diagforge._logging import get_logger
from diagforge.ingestion.base import IngestionError, TraceParser
from diagforge.ingestion.models import TraceEvent

_log = get_logger(__name__)


class AscTraceParser(TraceParser):
    """Wraps `can.ASCReader` and normalizes its output to TraceEvent."""

    extensions = (".asc",)

    def parse(self, path: Path) -> list[TraceEvent]:
        if not path.exists():
            raise IngestionError(f"trace file not found: {path}")
        if not path.is_file():
            raise IngestionError(f"trace path is not a regular file: {path}")
        try:
            reader = can.ASCReader(str(path))
        except (ValueError, can.CanError) as exc:
            raise IngestionError(f"ASC parser refused {path.name}: {exc}") from exc

        events: list[TraceEvent] = []
        t0: float | None = None
        try:
            for msg in reader:
                if msg.is_error_frame:
                    # Error frames are bus-level events; T0L scope keeps to data frames.
                    continue
                if t0 is None:
                    t0 = msg.timestamp
                # python-can returns floats in seconds; convert to int microseconds.
                ts_us = int(round((msg.timestamp - t0) * 1_000_000))
                if ts_us < 0:
                    ts_us = 0
                events.append(
                    TraceEvent(
                        timestamp_us=ts_us,
                        channel=msg.channel if isinstance(msg.channel, int) else 0,
                        frame_id=int(msg.arbitration_id),
                        is_extended=bool(msg.is_extended_id),
                        is_fd=bool(msg.is_fd),
                        dlc=int(msg.dlc),
                        data=bytes(msg.data),
                        decoded_signals=None,
                    )
                )
        except (ValueError, OSError) as exc:
            raise IngestionError(f"ASC parser failed mid-file on {path.name}: {exc}") from exc

        _log.info("parsed %d ASC frames from %s", len(events), path.name)
        return events
