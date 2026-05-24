"""Tests for the Layer 1 parsers: ASC trace + DTC JSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from diagforge.ingestion.base import IngestionError
from diagforge.ingestion.can_asc import AscTraceParser
from diagforge.ingestion.dtc_json import DtcJsonParser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# ---------- DTC JSON parser ----------


class TestDtcJsonParser:
    def test_parses_valid_file(self) -> None:
        parser = DtcJsonParser()
        out = parser.parse(FIXTURES / "dtcs_valid.json")
        assert len(out) == 2
        assert out[0].dtc_code == "P0300"
        assert out[1].standard == "uds"
        assert out[1].status_byte == 47

    def test_missing_file_raises(self) -> None:
        with pytest.raises(IngestionError, match="not found"):
            DtcJsonParser().parse(Path("/nonexistent/path.json"))

    def test_not_an_object_raises(self) -> None:
        with pytest.raises(IngestionError, match="root must be an object"):
            DtcJsonParser().parse(FIXTURES / "dtcs_not_object.json")

    def test_missing_dtcs_key_raises(self) -> None:
        with pytest.raises(IngestionError, match="top-level 'dtcs'"):
            DtcJsonParser().parse(FIXTURES / "dtcs_missing_dtcs.json")

    def test_invalid_entry_propagates_validation_error(self) -> None:
        with pytest.raises(IngestionError, match="failed validation"):
            DtcJsonParser().parse(FIXTURES / "dtcs_bad_entry.json")

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.json"
        broken.write_text("{not: valid")
        with pytest.raises(IngestionError, match="not valid JSON"):
            DtcJsonParser().parse(broken)

    def test_dtcs_value_not_a_list(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text('{"dtcs": "string-not-list"}')
        with pytest.raises(IngestionError, match="top-level 'dtcs'"):
            DtcJsonParser().parse(bad)

    def test_dtc_entry_not_object(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text('{"dtcs": ["not-an-object"]}')
        with pytest.raises(IngestionError, match=r"dtcs\[0\] must be an object"):
            DtcJsonParser().parse(bad)

    def test_can_parse_extension(self) -> None:
        assert DtcJsonParser.can_parse(Path("foo.json"))
        assert not DtcJsonParser.can_parse(Path("foo.txt"))


# ---------- ASC trace parser ----------


class TestAscTraceParser:
    def test_parses_tiny_fixture(self) -> None:
        out = AscTraceParser().parse(FIXTURES / "trace_tiny.asc")
        # python-can will read all frame lines; tolerate the parser dropping headers
        assert len(out) >= 2
        # timestamps are monotonic and normalized to zero
        from itertools import pairwise

        assert out[0].timestamp_us == 0
        for prev, cur in pairwise(out):
            assert cur.timestamp_us >= prev.timestamp_us
        # Frame data is preserved
        ids = {ev.frame_id for ev in out}
        assert 0x100 in ids

    def test_missing_file_raises(self) -> None:
        with pytest.raises(IngestionError, match="not found"):
            AscTraceParser().parse(Path("/nope/nada.asc"))

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IngestionError, match="not a regular file"):
            AscTraceParser().parse(tmp_path)

    def test_corrupt_fixture_yields_empty_or_error(self) -> None:
        # python-can's ASCReader is forgiving — it returns an empty iterator
        # on a file with no parseable lines. Either outcome is acceptable as
        # long as we don't crash uncaught.
        try:
            out = AscTraceParser().parse(FIXTURES / "trace_corrupt.asc")
        except IngestionError:
            return
        assert out == []

    def test_can_parse_extension(self) -> None:
        assert AscTraceParser.can_parse(Path("trace.asc"))
        assert not AscTraceParser.can_parse(Path("trace.blf"))
