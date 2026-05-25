"""Unit tests for the SignalDecoder bridge (DBC + auto modes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from diagforge.ingestion.base import IngestionError
from diagforge.ingestion.models import TraceEvent
from diagforge.ingestion.signal_decode import SignalDecoder

DBC = Path(__file__).resolve().parents[2] / "examples" / "p0300_intermittent_misfire" / "engine.dbc"


def _ev(frame_id: int, data: bytes, dlc: int | None = None) -> TraceEvent:
    return TraceEvent(
        timestamp_us=0,
        channel=0,
        frame_id=frame_id,
        dlc=dlc if dlc is not None else len(data),
        data=data,
    )


class TestAutoDecoder:
    def test_decodes_first_two_bytes_little_endian(self) -> None:
        d = SignalDecoder(None)
        events = d.decode([_ev(0x100, b"\x80\x0c\x00\x00\x00\x00\x00\x00", dlc=8)])
        assert events[0].decoded_signals == {"frame_0x100": 3200.0}

    def test_empty_payload_yields_no_signal(self) -> None:
        d = SignalDecoder(None)
        events = d.decode([_ev(0x200, b"", dlc=0)])
        assert events[0].decoded_signals is None

    def test_single_byte_payload(self) -> None:
        d = SignalDecoder(None)
        events = d.decode([_ev(0x300, b"\x42", dlc=1)])
        assert events[0].decoded_signals == {"frame_0x300": 0x42}

    def test_preserves_original_event_fields(self) -> None:
        d = SignalDecoder(None)
        ev = _ev(0x100, b"\x80\x0c\x00\x00\x00\x00\x00\x00", dlc=8)
        decoded_events = d.decode([ev])
        assert decoded_events[0].timestamp_us == ev.timestamp_us
        assert decoded_events[0].frame_id == ev.frame_id


class TestDbcDecoder:
    def test_decodes_engine_rpm_from_real_dbc(self) -> None:
        d = SignalDecoder(DBC)
        # 800 RPM = raw 3200 = 0x0C80 little-endian → bytes 80 0C
        events = d.decode([_ev(0x100, b"\x80\x0c\x00\x00\x00\x00\x00\x00", dlc=8)])
        assert events[0].decoded_signals is not None
        assert events[0].decoded_signals["engine_rpm"] == pytest.approx(800.0)

    def test_unknown_frame_id_returns_no_signal(self) -> None:
        d = SignalDecoder(DBC)
        events = d.decode([_ev(0x7E8, b"\x00" * 8, dlc=8)])
        assert events[0].decoded_signals is None

    def test_missing_dbc_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IngestionError, match="unable to load DBC"):
            SignalDecoder(tmp_path / "doesnotexist.dbc")

    def test_bad_dbc_format_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "garbage.dbc"
        bad.write_text("this is not a valid DBC")
        with pytest.raises(IngestionError, match="unable to load DBC"):
            SignalDecoder(bad)
