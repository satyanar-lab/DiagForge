# ADR 004 — Auto-decoder for ASC traces without a DBC

**Status:** accepted (T0L.7)
**Date:** 2026-05-24

`SignalDecoder` runs in two modes:

* **DBC mode** — `cantools` decodes each frame into named, scaled engineering
  units. This is the recommended path for any real workflow and is what the
  P0300 demo uses (`engine.dbc`).

* **Auto mode** — used when `--dbc` is omitted. Each frame gets a single
  synthetic signal named `frame_0x<id_hex>` whose value is the little-endian
  unsigned integer of the first two payload bytes. This is enough for the
  analyzer to detect dropouts and spikes on simple single-payload frames
  without requiring the user to author a DBC just to inspect a trace.

The trade-off: auto mode collapses everything to one synthetic signal per
frame ID, so multi-signal frames (typical in production CAN buses) lose
information. Findings then reference `frame_0x100` instead of `engine_rpm`,
which is less readable for the LLM. We accept this for Phase 0-Lite — DBC
mode is the supported workflow, auto mode is the safety net.

Phase 0 will add BLF, UDS, and J1939 ingestion; if the auto-decoder becomes
inadequate (e.g. multi-byte signals get truncated) we revisit and add a
per-frame heuristic that picks the dominant 16- or 32-bit field.
