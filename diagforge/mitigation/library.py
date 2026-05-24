"""Load mitigation patterns from packaged YAML files.

YAML is shipped in `diagforge/mitigation/data/`. New pattern files can be
dropped in (any `*.yaml`) and they will be merged on load. Pattern IDs must
be globally unique — duplicate IDs raise immediately so a bad merge fails
loudly rather than silently shadowing.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_DATA_PACKAGE: Final[str] = "diagforge.mitigation.data"

PatternType = str  # YAML-supplied; not enumerated so external libraries can extend


class PatternParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    suggestion_rule: str
    type: PatternType


class MitigationPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern_id: str
    name: str
    short_description: str
    when_applies: list[str] = Field(default_factory=list)
    parameters: list[PatternParameter] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    standards: list[str] = Field(default_factory=list)


class MitigationLibraryError(Exception):
    """Raised on duplicate IDs, bad YAML, or schema-version mismatch."""


class MitigationLibrary:
    """Indexed view over one or more mitigation pattern YAML files."""

    EXPECTED_SCHEMA_VERSION: Final[str] = "1.0.0"

    def __init__(self, patterns: dict[str, MitigationPattern]) -> None:
        self._patterns = patterns

    def __len__(self) -> int:
        return len(self._patterns)

    def list_pattern_ids(self) -> list[str]:
        return sorted(self._patterns)

    def get_by_id(self, pattern_id: str) -> MitigationPattern | None:
        return self._patterns.get(pattern_id)

    @classmethod
    def from_packaged_data(cls) -> MitigationLibrary:
        """Load every *.yaml in the packaged data directory."""
        merged: dict[str, MitigationPattern] = {}
        for entry in resources.files(_DATA_PACKAGE).iterdir():
            if entry.name.endswith(".yaml") or entry.name.endswith(".yml"):
                with resources.as_file(entry) as path:
                    cls._merge(merged, cls._load_one(Path(path)))
        return cls(merged)

    @classmethod
    def from_paths(cls, paths: list[Path]) -> MitigationLibrary:
        merged: dict[str, MitigationPattern] = {}
        for p in paths:
            cls._merge(merged, cls._load_one(p))
        return cls(merged)

    @staticmethod
    def _merge(into: dict[str, MitigationPattern], new: list[MitigationPattern]) -> None:
        for p in new:
            if p.pattern_id in into:
                raise MitigationLibraryError(f"duplicate pattern_id '{p.pattern_id}'")
            into[p.pattern_id] = p

    @classmethod
    def _load_one(cls, path: Path) -> list[MitigationPattern]:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise MitigationLibraryError(f"YAML parse error in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise MitigationLibraryError(f"YAML root in {path} must be a mapping")
        schema_version = raw.get("schema_version")
        if schema_version != cls.EXPECTED_SCHEMA_VERSION:
            raise MitigationLibraryError(
                f"unsupported schema_version {schema_version!r} in {path} "
                f"(expected {cls.EXPECTED_SCHEMA_VERSION!r})"
            )
        entries = raw.get("patterns") or []
        if not isinstance(entries, list):
            raise MitigationLibraryError(f"'patterns' must be a list in {path}")
        out: list[MitigationPattern] = []
        for i, entry in enumerate(entries):
            try:
                out.append(MitigationPattern.model_validate(entry))
            except ValidationError as exc:
                raise MitigationLibraryError(
                    f"pattern[{i}] in {path} failed validation: {exc}"
                ) from exc
        return out
