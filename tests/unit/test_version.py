"""Smoke test: package imports and exposes a version string."""

from __future__ import annotations

import diagforge


def test_version_is_semver_like() -> None:
    assert isinstance(diagforge.__version__, str)
    parts = diagforge.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
