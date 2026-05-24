"""DTC snapshot JSON parser.

Input shape — one object with a `dtcs` array. See claude/dtc-input-format.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from diagforge._logging import get_logger
from diagforge.ingestion.base import DtcParser, IngestionError
from diagforge.ingestion.models import DTCSnapshot

_log = get_logger(__name__)


class DtcJsonParser(DtcParser):
    """Read a JSON file conforming to the DTC input format spec."""

    extensions = (".json",)

    def parse(self, path: Path) -> list[DTCSnapshot]:
        if not path.exists():
            raise IngestionError(f"DTC file not found: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise IngestionError(f"unable to read DTC file {path}: {exc}") from exc

        try:
            blob = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"DTC file is not valid JSON ({path}): {exc}") from exc

        if not isinstance(blob, dict):
            raise IngestionError(f"DTC file root must be an object, got {type(blob).__name__}")

        if "dtcs" not in blob or not isinstance(blob["dtcs"], list):
            raise IngestionError("DTC file must have a top-level 'dtcs' array")

        snapshots: list[DTCSnapshot] = []
        for idx, entry in enumerate(blob["dtcs"]):
            if not isinstance(entry, dict):
                raise IngestionError(f"dtcs[{idx}] must be an object")
            try:
                snapshots.append(DTCSnapshot.model_validate(entry))
            except ValidationError as exc:
                raise IngestionError(f"dtcs[{idx}] failed validation: {exc}") from exc

        _log.info("parsed %d DTC snapshots from %s", len(snapshots), path.name)
        return snapshots
