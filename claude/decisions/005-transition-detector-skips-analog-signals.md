# ADR 005 — Transition-anomaly detector skips analog signals

**Status:** accepted (T0L.7)
**Date:** 2026-05-24

`detect_transition_anomalies` is designed for **discrete** signals (door
switch, brake switch, ignition line). The first integration run on the
P0300 demo trace showed it firing constantly on `engine_rpm` — an analog
signal whose 800 RPM idle wobbles ±10 RPM every frame, easily exceeding 5
changes per 50 ms by base rate alone.

Fix: skip signals whose codomain has more than 10 distinct values in the
window. A bouncing switch typically has 2; a noisy analog reading has many
hundreds. The threshold is conservative — 3-state valves still flow through
the detector, but real analog readings short-circuit cleanly.

Trade-off: a very-low-rate analog signal whose value happens to step through
< 10 unique values in the window will be incorrectly classified as discrete.
We have not seen this in practice (most analog readings have many distinct
values due to noise) and accept it as a minor false-positive risk to
preserve readability of the report on the demo case.

Alternative considered: explicit per-signal type metadata from the DBC.
`cantools` does carry a "discrete" flag in some DBCs, but not all DBCs
populate it, and Phase 0-Lite does not yet wire DBC metadata through.
Phase 0 may revisit.
