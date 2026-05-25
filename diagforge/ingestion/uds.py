"""UDS .log trace parser (ISO 14229 over ISO-TP, carried on CAN).

Phase 0 scope: read python-can `.log` (canutils) format frames and surface
UDS service-level information as decoded signals on each TraceEvent. We do
the lightweight PCI/SID parse inline because the analyzer only needs to see
service IDs, subfunctions, and NRCs to spot interesting patterns
(repeated negative-response 0x22, ReadDTCInformation bursts, session
control round-trips). Full ISO-TP reassembly across multi-frame messages
is deferred to Phase 1 — multi-frame responses still emit one TraceEvent
per CAN frame, with the first-frame PCI reflected in decoded_signals.

The `udsoncan` library is installed (Phase 0 dependency, per CLAUDE.md) and
will be used for richer service-name decoding once the analyzer wants it;
the current parse intentionally stays close to the wire so the tool keeps
working on logs from buses whose services aren't yet in udsoncan's table.
"""

from __future__ import annotations

from pathlib import Path

import can

from diagforge._logging import get_logger
from diagforge.ingestion.base import IngestionError, TraceParser
from diagforge.ingestion.models import TraceEvent

_log = get_logger(__name__)

# ISO 15765-2 PCI frame types (high nibble of byte 0).
_ISO_TP_SINGLE_FRAME = 0x0
_ISO_TP_FIRST_FRAME = 0x1
_ISO_TP_CONSECUTIVE_FRAME = 0x2
_ISO_TP_FLOW_CONTROL = 0x3

#: UDS negative response service ID (ISO 14229-1 §7.5.2).
_UDS_NEGATIVE_RESPONSE = 0x7F

#: UDS request CAN IDs are conventionally 0x7E0-0x7E7 (physical, 11-bit).
#: Responses are 0x7E8-0x7EF. 0x7DF is functional broadcast.
_UDS_REQUEST_ID_RANGE = range(0x7E0, 0x7E8)
_UDS_RESPONSE_ID_RANGE = range(0x7E8, 0x7F0)
_UDS_FUNCTIONAL_BROADCAST = 0x7DF


def _is_uds_id(frame_id: int) -> bool:
    return (
        frame_id == _UDS_FUNCTIONAL_BROADCAST
        or frame_id in _UDS_REQUEST_ID_RANGE
        or frame_id in _UDS_RESPONSE_ID_RANGE
    )


def _decode_uds_pci(data: bytes) -> dict[str, float] | None:
    """Extract service ID / subfunction / NRC from an ISO-TP single frame.

    Returns None when the frame is not a single-frame UDS message we can
    decode (consecutive frames, flow control, runt frames). Multi-frame
    first frames return at least the service ID so the analyzer can still
    see them.
    """
    if len(data) < 2:
        return None
    pci_high = (data[0] >> 4) & 0x0F

    if pci_high == _ISO_TP_SINGLE_FRAME:
        sf_length = data[0] & 0x0F
        if sf_length == 0 or sf_length > min(7, len(data) - 1):
            return None
        sid = data[1]
        return _decode_uds_payload(sid, data[1 : 1 + sf_length])

    if pci_high == _ISO_TP_FIRST_FRAME:
        # First frame: PCI byte 0 = (0x1 << 4) | (total_length_high4)
        #              PCI byte 1 = total_length_low8; data starts at byte 2.
        if len(data) < 3:
            return None
        total_length = ((data[0] & 0x0F) << 8) | data[1]
        sid = data[2]
        return {
            "uds_service_id": float(sid),
            "uds_frame_type": float(_ISO_TP_FIRST_FRAME),
            "uds_total_length": float(total_length),
        }

    if pci_high == _ISO_TP_CONSECUTIVE_FRAME:
        seq = data[0] & 0x0F
        return {
            "uds_frame_type": float(_ISO_TP_CONSECUTIVE_FRAME),
            "uds_sequence_index": float(seq),
        }

    if pci_high == _ISO_TP_FLOW_CONTROL:
        # FC byte 0 = (0x3 << 4) | flow_status (CTS=0, WT=1, OVFLW=2)
        if len(data) < 3:
            return None
        return {
            "uds_frame_type": float(_ISO_TP_FLOW_CONTROL),
            "uds_flow_status": float(data[0] & 0x0F),
            "uds_block_size": float(data[1]),
            "uds_st_min": float(data[2]),
        }

    return None


def _decode_uds_payload(sid: int, payload: bytes) -> dict[str, float]:
    """Extract richer fields from a UDS single-frame service payload."""
    out: dict[str, float] = {
        "uds_service_id": float(sid),
        "uds_frame_type": float(_ISO_TP_SINGLE_FRAME),
    }
    if sid == _UDS_NEGATIVE_RESPONSE and len(payload) >= 3:
        # Negative response: [0x7F, requested_sid, nrc]
        out["uds_requested_sid"] = float(payload[1])
        out["uds_nrc"] = float(payload[2])
    elif len(payload) >= 2:
        # Most services have a subfunction byte as the next byte after the SID.
        out["uds_subfunction"] = float(payload[1])
    return out


class UdsLogParser(TraceParser):
    """Wraps `can.LogReader` and adds UDS-aware decoded_signals on each event."""

    extensions = (".log",)

    def parse(self, path: Path) -> list[TraceEvent]:
        if not path.exists():
            raise IngestionError(f"trace file not found: {path}")
        if not path.is_file():
            raise IngestionError(f"trace path is not a regular file: {path}")
        try:
            reader = can.LogReader(str(path))
        except (ValueError, can.CanError) as exc:
            raise IngestionError(f"LogReader refused {path.name}: {exc}") from exc

        events: list[TraceEvent] = []
        t0: float | None = None
        try:
            for msg in reader:
                if msg.is_error_frame:
                    continue
                if t0 is None:
                    t0 = msg.timestamp
                ts_us = int(round((msg.timestamp - t0) * 1_000_000))
                if ts_us < 0:
                    ts_us = 0
                decoded: dict[str, float] | None = None
                if _is_uds_id(int(msg.arbitration_id)):
                    decoded = _decode_uds_pci(bytes(msg.data))
                events.append(
                    TraceEvent(
                        timestamp_us=ts_us,
                        channel=msg.channel if isinstance(msg.channel, int) else 0,
                        frame_id=int(msg.arbitration_id),
                        is_extended=bool(msg.is_extended_id),
                        is_fd=bool(msg.is_fd),
                        dlc=int(msg.dlc),
                        data=bytes(msg.data),
                        decoded_signals=decoded,
                    )
                )
        except (ValueError, OSError) as exc:
            raise IngestionError(f"LogReader failed mid-file on {path.name}: {exc}") from exc

        _log.info("parsed %d frames from UDS .log %s", len(events), path.name)
        return events
