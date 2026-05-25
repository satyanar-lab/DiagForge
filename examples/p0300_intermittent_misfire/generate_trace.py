#!/usr/bin/env python3
"""Reproducible generator for the P0300 intermittent-misfire demo trace.

The trace is checked in (`trace.asc`), but this script lets reviewers reproduce
it byte-for-byte from the seed. Run from the example directory:

    python3 generate_trace.py > trace.asc

The synthetic scenario:
  * CAN ID 0x100 carries `engine_rpm` as little-endian uint16, 0.25 RPM/bit
    (matches `engine.dbc`).
  * ~100 Hz framing with ±2 ms timestamp jitter.
  * 50 idle frames at ~800 RPM with ±10 RPM noise.
  * 4 misfire dropouts, each 3 frames wide, with varied dropout depths and
    irregular spacing — emulating intermittent-misfire-induced RPM sag.
  * 20 idle frames after, then end of measurement.
"""

from __future__ import annotations

import random
import sys
from typing import TextIO

SEED = 0xD1A9F0_7E  # picked because it produced credibly irregular jitter on inspection
IDLE_RPM = 800.0
IDLE_NOISE = 10.0
FRAME_PERIOD_S = 0.01
FRAME_JITTER_S = 0.002
NUM_PRE_IDLE = 50
NUM_POST_IDLE = 20

# (dropout_depth_rpm, depth_jitter_rpm, frames_in_dropout, idle_frames_before_next)
DROPOUTS: list[tuple[float, float, int, int]] = [
    (55.0, 8.0, 3, 4),   # deep first dip, 4 idle frames before the next
    (95.0, 6.0, 3, 3),
    (72.0, 5.0, 3, 5),
    (130.0, 9.0, 3, 0),  # last dropout (shallowest) — slightly broader band
]


def encode_rpm(rpm: float) -> bytes:
    raw = max(0, min(0xFFFF, int(round(rpm / 0.25))))
    return raw.to_bytes(2, "little") + b"\x00" * 6


def format_frame(timestamp_s: float, frame_id: int, data: bytes) -> str:
    hex_bytes = " ".join(f"{b:02X}" for b in data)
    return f"   {timestamp_s:9.6f} 1  {frame_id:X}             Rx   d {len(data)} {hex_bytes}\n"


def write_trace(out: TextIO) -> None:
    rng = random.Random(SEED)
    out.write("date Sun May 24 12:00:00 PM 2026\n")
    out.write("base hex  timestamps absolute\n")
    out.write("internal events logged\n")
    out.write("// version 11.0.0\n")
    out.write("Begin Triggerblock Sun May 24 12:00:00 PM 2026\n")
    out.write("   0.000000 Start of measurement\n")

    ts = 0.0

    def next_ts() -> float:
        nonlocal ts
        ts += FRAME_PERIOD_S + rng.uniform(-FRAME_JITTER_S * 0.2, FRAME_JITTER_S * 0.2)
        return ts

    # pre-dropout idle
    for _ in range(NUM_PRE_IDLE):
        rpm = IDLE_RPM + rng.uniform(-IDLE_NOISE, IDLE_NOISE)
        out.write(format_frame(next_ts(), 0x100, encode_rpm(rpm)))

    # 4 dropouts with idle frames between
    for depth, depth_jitter, n_frames, idle_after in DROPOUTS:
        for _ in range(n_frames):
            rpm = depth + rng.uniform(-depth_jitter, depth_jitter)
            out.write(format_frame(next_ts(), 0x100, encode_rpm(rpm)))
        for _ in range(idle_after):
            rpm = IDLE_RPM + rng.uniform(-IDLE_NOISE, IDLE_NOISE)
            out.write(format_frame(next_ts(), 0x100, encode_rpm(rpm)))

    # post-dropout idle
    for _ in range(NUM_POST_IDLE):
        rpm = IDLE_RPM + rng.uniform(-IDLE_NOISE, IDLE_NOISE)
        out.write(format_frame(next_ts(), 0x100, encode_rpm(rpm)))

    out.write("End TriggerBlock\n")


if __name__ == "__main__":
    write_trace(sys.stdout)
