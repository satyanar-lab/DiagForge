"""Pydantic models for the ingestion layer (Layer 1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

DtcStandard = Literal["obd2", "uds", "j1939"]


class TraceEvent(BaseModel):
    """One normalized CAN/CAN-FD frame from a trace file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp_us: int = Field(ge=0, description="microseconds since trace start")
    channel: int = Field(ge=0)
    frame_id: int = Field(ge=0, description="CAN identifier (11- or 29-bit)")
    is_extended: bool = False
    is_fd: bool = False
    dlc: int = Field(ge=0, le=64, description="data length code (0-64 for CAN-FD)")
    data: bytes = b""
    decoded_signals: dict[str, float] | None = None

    @field_validator("frame_id")
    @classmethod
    def _frame_id_within_range(cls, value: int) -> int:
        if value > 0x1FFFFFFF:
            raise ValueError("frame_id exceeds 29-bit extended CAN identifier range")
        return value


class DTCSnapshot(BaseModel):
    """One DTC observation from the input snapshot file."""

    model_config = ConfigDict(extra="forbid")

    dtc_code: str = Field(min_length=1, description="e.g. P0420, U0100, UDS DID hex")
    standard: DtcStandard
    status_byte: int | None = Field(default=None, ge=0, le=255)
    timestamp_first_us: int = Field(ge=0)
    timestamp_latest_us: int = Field(ge=0)
    occurrence_count: int = Field(ge=1)
    description: str | None = None

    @field_validator("timestamp_latest_us")
    @classmethod
    def _latest_after_first(cls, value: int, info: ValidationInfo) -> int:
        first = info.data.get("timestamp_first_us")
        if first is not None and value < first:
            raise ValueError("timestamp_latest_us must be >= timestamp_first_us")
        return value
