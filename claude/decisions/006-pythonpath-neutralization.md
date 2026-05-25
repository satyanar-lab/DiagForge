# ADR 006 — Makefile neutralizes PYTHONPATH and PYTHONNOUSERSITE

**Status:** accepted (T0L.1)
**Date:** 2026-05-24

Some development hosts (typically those with ROS 2 or system-wide
`dist-packages` installations) set `PYTHONPATH=/opt/ros/.../site-packages`.
This poisons the poetry venv because pytest auto-discovers plugin entry
points (`launch_testing`, etc.) from anything on `sys.path` and then crashes
when those plugins try to import dependencies (`lark`, `osrf-pycommon`) that
aren't in our `pyproject.toml`.

The Makefile defensively exports `PYTHONPATH=""` and `PYTHONNOUSERSITE=1` so
every `make lint`, `make test`, `make demo` invocation runs with a clean,
project-local interpreter regardless of the developer's shell environment.

Trade-off: a developer who wants to expose their own PYTHONPATH packages to
DiagForge must run `poetry run ...` directly rather than via `make`. This is
the right default for CI-style reproducibility.
