"""Centralized logging configuration for DiagForge."""

from __future__ import annotations

import logging
import sys
from typing import Final

_DEFAULT_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


def configure_logging(verbose: bool = False) -> None:
    """Install the root handler. Idempotent — safe to call multiple times."""
    global _configured
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_DEFAULT_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. Call configure_logging() once at entry point."""
    return logging.getLogger(name)
