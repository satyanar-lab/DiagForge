"""Tests for the UDS .log parser and the trace-parser registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from diagforge.ingestion.base import IngestionError
from diagforge.ingestion.can_asc import AscTraceParser
from diagforge.ingestion.registry import (
    format_for,
    parser_for,
    supported_extensions,
)
from diagforge.ingestion.uds import (
    UdsLogParser,
    _decode_uds_pci,
    _is_uds_id,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# ---------- ISO-TP PCI decoding ----------


class TestUdsPciDecode:
    def test_single_frame_session_control(self) -> None:
        # 02 10 03 — SF length 2, service 0x10 (DiagnosticSessionControl), subfn 0x03.
        out = _decode_uds_pci(b"\x02\x10\x03\x00\x00\x00\x00\x00")
        assert out == {
            "uds_service_id": 0x10,
            "uds_frame_type": 0.0,
            "uds_subfunction": 0x03,
        }

    def test_negative_response_includes_requested_sid_and_nrc(self) -> None:
        out = _decode_uds_pci(b"\x03\x7f\x22\x31\x00\x00\x00\x00")
        assert out == {
            "uds_service_id": 0x7F,
            "uds_frame_type": 0.0,
            "uds_requested_sid": 0x22,
            "uds_nrc": 0x31,
        }

    def test_first_frame_includes_total_length(self) -> None:
        # 10 4F 59 02 ... — FF, total length 0x04F = 79, service 0x59.
        out = _decode_uds_pci(b"\x10\x4f\x59\x02\x00\x01\x02\x01")
        assert out == {
            "uds_service_id": 0x59,
            "uds_frame_type": 1.0,
            "uds_total_length": float(0x04F),
        }

    def test_consecutive_frame_sequence_index(self) -> None:
        out = _decode_uds_pci(b"\x21\x03\xa1\x02\x00\x00\x00\x00")
        assert out == {
            "uds_frame_type": 2.0,
            "uds_sequence_index": 1.0,
        }

    def test_flow_control_cts(self) -> None:
        out = _decode_uds_pci(b"\x30\x08\x14\x00\x00\x00\x00\x00")
        assert out == {
            "uds_frame_type": 3.0,
            "uds_flow_status": 0.0,
            "uds_block_size": 8.0,
            "uds_st_min": 0x14,
        }

    def test_runt_frame_returns_none(self) -> None:
        assert _decode_uds_pci(b"\x02") is None
        assert _decode_uds_pci(b"") is None

    def test_sf_length_too_large_returns_none(self) -> None:
        # SF length claims 7 bytes but only has 2 in payload.
        assert _decode_uds_pci(b"\x07\x10") is None

    def test_sf_length_zero_returns_none(self) -> None:
        assert _decode_uds_pci(b"\x00\x10\x00\x00\x00\x00\x00\x00") is None


class TestUdsIdRange:
    def test_request_range(self) -> None:
        assert _is_uds_id(0x7E0)
        assert _is_uds_id(0x7E7)
        assert not _is_uds_id(0x7DE)

    def test_response_range(self) -> None:
        assert _is_uds_id(0x7E8)
        assert _is_uds_id(0x7EF)
        assert not _is_uds_id(0x7F0)

    def test_functional_broadcast(self) -> None:
        assert _is_uds_id(0x7DF)


# ---------- UdsLogParser file-level ----------


class TestUdsLogParser:
    def test_parses_tiny_fixture(self) -> None:
        events = UdsLogParser().parse(FIXTURES / "uds_tiny.log")
        assert len(events) == 7
        # Timestamps normalized to start at zero.
        assert events[0].timestamp_us == 0
        # SessionControl request: service 0x10
        assert events[0].decoded_signals == {
            "uds_service_id": 0x10,
            "uds_frame_type": 0.0,
            "uds_subfunction": 0x03,
        }
        # Positive response: service 0x50 (0x10 + 0x40)
        assert events[1].decoded_signals is not None
        assert events[1].decoded_signals["uds_service_id"] == 0x50
        # ReadDTCInfo request: service 0x19
        assert events[2].decoded_signals is not None
        assert events[2].decoded_signals["uds_service_id"] == 0x19
        # First-frame response with total_length 0x04F = 79
        assert events[3].decoded_signals == {
            "uds_service_id": 0x59,
            "uds_frame_type": 1.0,
            "uds_total_length": float(0x04F),
        }
        # ConsecutiveFrame seq 1
        assert events[4].decoded_signals == {
            "uds_frame_type": 2.0,
            "uds_sequence_index": 1.0,
        }
        # Negative response 0x22/0x31
        assert events[6].decoded_signals is not None
        assert events[6].decoded_signals["uds_service_id"] == 0x7F
        assert events[6].decoded_signals["uds_nrc"] == 0x31

    def test_missing_file_raises(self) -> None:
        with pytest.raises(IngestionError, match="not found"):
            UdsLogParser().parse(Path("/nope/no.log"))

    def test_directory_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IngestionError, match="not a regular file"):
            UdsLogParser().parse(tmp_path)

    def test_garbage_file_does_not_crash(self) -> None:
        # python-can's LogReader is forgiving on unparseable lines; it returns
        # an empty iterator. Either an empty result or an IngestionError is OK,
        # but it must not crash uncaught.
        try:
            out = UdsLogParser().parse(FIXTURES / "uds_garbage.log")
        except IngestionError:
            return
        assert out == []

    def test_non_uds_id_leaves_decoded_signals_none(self, tmp_path: Path) -> None:
        f = tmp_path / "noise.log"
        f.write_text("(1.0) vcan0 100#0102030405060708\n")
        events = UdsLogParser().parse(f)
        assert len(events) == 1
        assert events[0].decoded_signals is None

    def test_can_parse_extension(self) -> None:
        assert UdsLogParser.can_parse(Path("foo.log"))
        assert not UdsLogParser.can_parse(Path("foo.asc"))


# ---------- registry ----------


class TestRegistry:
    def test_supported_extensions_includes_asc_and_log(self) -> None:
        exts = supported_extensions()
        assert ".asc" in exts
        assert ".log" in exts

    def test_parser_for_asc_returns_ascparser(self) -> None:
        p = parser_for(Path("/tmp/trace.asc"))
        assert isinstance(p, AscTraceParser)

    def test_parser_for_log_returns_udsparser(self) -> None:
        p = parser_for(Path("/tmp/trace.log"))
        assert isinstance(p, UdsLogParser)

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(IngestionError, match="no trace parser"):
            parser_for(Path("/tmp/trace.unknown"))

    def test_format_for_strips_dot_and_lowercases(self) -> None:
        assert format_for(Path("/tmp/trace.ASC")) == "asc"
        assert format_for(Path("/tmp/trace.log")) == "log"
