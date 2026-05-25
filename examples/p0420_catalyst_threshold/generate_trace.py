#!/usr/bin/env python3
"""Reproducible generator for the P0420 catalyst-threshold demo trace.

Scenario:
  * Post-catalyst O2 sensor (CAN ID 0x401) publishes voltage every 20 ms.
  * Healthy post-cat behaviour is a flat ~0.1-0.2 V (low swing); a failing
    catalyst lets the upstream rich/lean swings through, so the signal
    spikes toward 0.8-0.9 V at the upstream switching frequency.
  * This trace captures the failing-cat shape: a low baseline with 8
    high-voltage spikes at irregular ~250-350 ms spacing, each ~80 ms wide.
  * P0420 latches on the third confirmed spike and persists.

Run from this directory:
    python3 generate_trace.py > trace.asc

Seed is committed for byte-for-byte reproducibility.
"""

from __future__ import annotations

import random
import sys
from typing import TextIO

SEED = 0xCA7A1_FA11
SAMPLE_PERIOD_S = 0.020
SAMPLE_JITTER_S = 0.003
LEAN_VOLTAGE_V = 0.12
LEAN_NOISE_V = 0.02
RICH_VOLTAGE_V = 0.85
RICH_NOISE_V = 0.04

# (spike_start_s, spike_duration_s) — irregular spacing, varied widths.
SPIKES: list[tuple[float, float]] = [
    (0.260, 0.080),
    (0.560, 0.090),
    (0.860, 0.070),
    (1.180, 0.085),
    (1.470, 0.080),
    (1.780, 0.095),
    (2.080, 0.075),
    (2.380, 0.085),
]
TRACE_END_S = 2.700


def encode_u16_le(value: int) -> bytes:
    return max(0, min(0xFFFF, value)).to_bytes(2, "little") + b"\x00" * 6


def fmt(ts_s: float, can_id: int, data: bytes) -> str:
    hex_bytes = " ".join(f"{b:02X}" for b in data)
    return f"   {ts_s:9.6f} 1  {can_id:X}             Rx   d {len(data)} {hex_bytes}\n"


def in_spike(ts: float) -> bool:
    return any(start <= ts < start + dur for start, dur in SPIKES)


def write_trace(out: TextIO) -> None:
    rng = random.Random(SEED)
    out.write("date Sun May 24 14:00:00 PM 2026\n")
    out.write("base hex  timestamps absolute\n")
    out.write("internal events logged\n")
    out.write("// version 11.0.0\n")
    out.write("Begin Triggerblock Sun May 24 14:00:00 PM 2026\n")
    out.write("   0.000000 Start of measurement\n")

    t = 0.0
    while t < TRACE_END_S:
        if in_spike(t):
            voltage = RICH_VOLTAGE_V + rng.uniform(-RICH_NOISE_V, RICH_NOISE_V)
        else:
            voltage = LEAN_VOLTAGE_V + rng.uniform(-LEAN_NOISE_V, LEAN_NOISE_V)
        raw = int(round(voltage * 1000))  # 0.001 V / bit
        out.write(fmt(t, 0x401, encode_u16_le(raw)))
        t += SAMPLE_PERIOD_S + rng.uniform(-SAMPLE_JITTER_S * 0.2, SAMPLE_JITTER_S * 0.2)

    out.write("End TriggerBlock\n")


if __name__ == "__main__":
    write_trace(sys.stdout)
