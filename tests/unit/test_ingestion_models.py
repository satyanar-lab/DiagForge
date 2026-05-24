"""Validation tests for the Layer 1 ingestion models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from diagforge.ingestion.models import DTCSnapshot, TraceEvent


class TestTraceEvent:
    def test_minimal_valid_event(self) -> None:
        ev = TraceEvent(
            timestamp_us=1_000,
            channel=0,
            frame_id=0x100,
            is_extended=False,
            is_fd=False,
            dlc=8,
            data=b"\x00" * 8,
        )
        assert ev.timestamp_us == 1_000
        assert ev.decoded_signals is None

    def test_extended_frame_id_at_max(self) -> None:
        ev = TraceEvent(timestamp_us=0, channel=0, frame_id=0x1FFFFFFF, dlc=0, is_extended=True)
        assert ev.frame_id == 0x1FFFFFFF

    def test_frame_id_above_29_bit_rejected(self) -> None:
        with pytest.raises(ValidationError, match="29-bit"):
            TraceEvent(timestamp_us=0, channel=0, frame_id=0x20000000, dlc=0)

    def test_negative_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TraceEvent(timestamp_us=-1, channel=0, frame_id=0, dlc=0)

    def test_round_trip_json(self) -> None:
        ev = TraceEvent(
            timestamp_us=42,
            channel=1,
            frame_id=0x7E8,
            dlc=4,
            data=b"\x01\x02\x03\x04",
            decoded_signals={"engine_rpm": 800.0},
        )
        round = TraceEvent.model_validate_json(ev.model_dump_json())
        assert round == ev


class TestDTCSnapshot:
    def test_minimal_valid(self) -> None:
        s = DTCSnapshot(
            dtc_code="P0300",
            standard="obd2",
            timestamp_first_us=0,
            timestamp_latest_us=100_000,
            occurrence_count=1,
        )
        assert s.status_byte is None

    def test_latest_before_first_rejected(self) -> None:
        with pytest.raises(ValidationError, match=">= timestamp_first_us"):
            DTCSnapshot(
                dtc_code="P0300",
                standard="obd2",
                timestamp_first_us=200,
                timestamp_latest_us=100,
                occurrence_count=1,
            )

    def test_occurrence_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DTCSnapshot(
                dtc_code="P0300",
                standard="obd2",
                timestamp_first_us=0,
                timestamp_latest_us=0,
                occurrence_count=0,
            )

    def test_status_byte_above_255_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DTCSnapshot(
                dtc_code="U0100",
                standard="uds",
                status_byte=300,
                timestamp_first_us=0,
                timestamp_latest_us=0,
                occurrence_count=1,
            )

    def test_unknown_standard_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DTCSnapshot(
                dtc_code="X1234",
                standard="proprietary",  # type: ignore[arg-type]
                timestamp_first_us=0,
                timestamp_latest_us=0,
                occurrence_count=1,
            )

    def test_round_trip_json(self) -> None:
        s = DTCSnapshot(
            dtc_code="U0100",
            standard="uds",
            status_byte=0x2F,
            timestamp_first_us=1_000,
            timestamp_latest_us=2_000,
            occurrence_count=3,
            description="Lost Communication With ECM/PCM A",
        )
        assert DTCSnapshot.model_validate_json(s.model_dump_json()) == s
