#!/usr/bin/env python3
"""Reproducible generator for the U0100 lost-communication demo trace.

The scenario:
  * ECM publishes engine_temp on CAN ID 0x300 every 10 ms.
  * Gateway publishes wheel_speed on CAN ID 0x200 every 20 ms.
  * Mid-trace the ECM goes silent four times — bus dropouts of irregular
    width (~280, 320, 250, 380 ms). The wheel_speed broadcast keeps going
    so the dropouts are unambiguously per-source, not a bus-level halt.
  * Idle quiet recovers after each gap, then resumes normal 10 ms publish.
  * After the third gap, the gateway latches U0100 (DTC anchored in the
    DTC JSON to that timestamp).

Run from this directory:
    python3 generate_trace.py > trace.asc

Seed is fixed and committed so the trace round-trips byte-for-byte.
"""

from __future__ import annotations

import random
import sys
from typing import TextIO

SEED = 0xC0FFEE_91
ECM_PERIOD_S = 0.010
ECM_JITTER_S = 0.001
GATEWAY_PERIOD_S = 0.020
GATEWAY_JITTER_S = 0.0015

# (start_s, duration_s) of ECM bus silence windows.
ECM_DROPOUTS: list[tuple[float, float]] = [
    (0.500, 0.280),
    (1.000, 0.320),
    (1.480, 0.250),
    (1.900, 0.380),
]

TRACE_END_S = 2.700


def encode_u16_le(value: int) -> bytes:
    return max(0, min(0xFFFF, value)).to_bytes(2, "little") + b"\x00" * 6


def fmt(ts_s: float, can_id: int, data: bytes) -> str:
    hex_bytes = " ".join(f"{b:02X}" for b in data)
    return f"   {ts_s:9.6f} 1  {can_id:X}             Rx   d {len(data)} {hex_bytes}\n"


def in_dropout(ts: float) -> bool:
    return any(start <= ts < start + dur for start, dur in ECM_DROPOUTS)


def write_trace(out: TextIO) -> None:
    rng = random.Random(SEED)
    out.write("date Sun May 24 13:00:00 PM 2026\n")
    out.write("base hex  timestamps absolute\n")
    out.write("internal events logged\n")
    out.write("// version 11.0.0\n")
    out.write("Begin Triggerblock Sun May 24 13:00:00 PM 2026\n")
    out.write("   0.000000 Start of measurement\n")

    # Schedule both sources independently with jitter and merge by timestamp.
    frames: list[tuple[float, int, bytes]] = []

    # ECM 0x300 (engine_temp ~ 88°C ± 0.5°C; arbitrary baseline since the test
    # is about presence/absence, not value).
    t = 0.0
    while t < TRACE_END_S:
        if not in_dropout(t):
            temp_c = 88.0 + rng.uniform(-0.5, 0.5)
            raw = int(round(temp_c * 100))  # 0.01 °C / bit
            frames.append((t, 0x300, encode_u16_le(raw)))
        t += ECM_PERIOD_S + rng.uniform(-ECM_JITTER_S * 0.2, ECM_JITTER_S * 0.2)

    # Gateway 0x200 (wheel_speed steady ~ 60 km/h ± noise).
    t = 0.0
    while t < TRACE_END_S:
        speed_kmh = 60.0 + rng.uniform(-0.3, 0.3)
        raw = int(round(speed_kmh * 100))
        frames.append((t, 0x200, encode_u16_le(raw)))
        t += GATEWAY_PERIOD_S + rng.uniform(-GATEWAY_JITTER_S * 0.2, GATEWAY_JITTER_S * 0.2)

    frames.sort(key=lambda x: x[0])
    for ts, cid, data in frames:
        out.write(fmt(ts, cid, data))

    out.write("End TriggerBlock\n")


if __name__ == "__main__":
    write_trace(sys.stdout)
