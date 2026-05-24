"""Bridge between raw TraceEvents and the signal-keyed analyzer interface.

Two decoders:

* **DBC decoder** — uses `cantools` to translate every frame whose ID is in
  the supplied DBC into named, scaled engineering values.

* **Auto decoder** — used when no DBC is provided. Emits a single synthetic
  signal per frame ID, named `frame_<id_hex>`, whose value is the little-
  endian unsigned integer of bytes 0..min(dlc, 2). This is enough for the
  analyzer to detect dropouts and spikes on a single-payload frame without
  needing a DBC for the demo.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import cantools

from diagforge._logging import get_logger
from diagforge.ingestion.base import IngestionError
from diagforge.ingestion.models import TraceEvent

_log = get_logger(__name__)


class SignalDecoder:
    """Mutates a TraceEvent stream to populate `decoded_signals` in-place."""

    def __init__(self, dbc_path: Path | None) -> None:
        self._dbc_path = dbc_path
        if dbc_path is not None:
            try:
                db = cantools.database.load_file(str(dbc_path))
            except (cantools.database.UnsupportedDatabaseFormatError, FileNotFoundError) as exc:
                raise IngestionError(f"unable to load DBC {dbc_path}: {exc}") from exc
            # cantools' loader returns either CAN or LIN; reject LIN for clarity.
            if not hasattr(db, "messages"):
                raise IngestionError(f"DBC {dbc_path} did not yield CAN messages")
            self._db = db
        else:
            self._db = None

    def decode(self, events: Iterable[TraceEvent]) -> list[TraceEvent]:
        out: list[TraceEvent] = []
        for ev in events:
            if self._db is not None:
                decoded = self._decode_via_dbc(ev)
            else:
                decoded = self._decode_via_auto(ev)
            if decoded:
                out.append(ev.model_copy(update={"decoded_signals": decoded}))
            else:
                out.append(ev)
        if self._dbc_path is not None:
            _log.info("decoded %d frames via DBC %s", len(out), self._dbc_path.name)
        else:
            _log.info("auto-decoded %d frames (no DBC supplied)", len(out))
        return out

    def _decode_via_dbc(self, ev: TraceEvent) -> dict[str, float] | None:
        assert self._db is not None
        try:
            msg = self._db.get_message_by_frame_id(ev.frame_id)
        except KeyError:
            return None
        try:
            decoded = msg.decode(ev.data, allow_truncated=True)
        except (ValueError, KeyError):
            return None
        out: dict[str, float] = {}
        for name, value in decoded.items():
            try:
                out[name] = float(value)
            except (TypeError, ValueError):
                # NamedSignalValue or similar enum: skip in Phase 0-Lite.
                continue
        return out or None

    @staticmethod
    def _decode_via_auto(ev: TraceEvent) -> dict[str, float] | None:
        if ev.dlc == 0 or not ev.data:
            return None
        n = min(ev.dlc, 2, len(ev.data))
        raw = int.from_bytes(ev.data[:n], "little", signed=False)
        return {f"frame_0x{ev.frame_id:03X}": float(raw)}
