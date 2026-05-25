# Demo case — U0100 Lost Communication With ECM/PCM "A"

This case reproduces the most common shape of a U0100 complaint: brief,
bounded periods of CAN silence from a specific source (the ECM publishing
on 0x300 in this scenario), with other traffic on the same bus continuing
normally. The gateway's lost-communication monitor latches U0100 after the
silence exceeds its timeout.

## Scenario

* `engine_temp` broadcast on CAN ID `0x300` from the ECM at 100 Hz
  (`engine_bus.dbc` defines a 16-bit little-endian unsigned signal,
  0.01 °C/bit).
* `wheel_speed` broadcast on CAN ID `0x200` from the gateway at 50 Hz —
  same DBC. This is **unaffected** by the ECM dropouts, so the trace
  unambiguously shows the silence is per-source, not bus-level.
* Four ECM-only silence windows in a ~2 s span:
  starts at 500/1000/1480/1900 ms with durations of 280/320/250/380 ms.
* DTC `U0100` anchored at the third dropout's onset, persisting through
  the fourth.

## Files

| File | Purpose |
|---|---|
| `trace.asc` | Synthetic ASC CAN log, ~280 frames, ~2.7 s duration |
| `engine_bus.dbc` | DBC defining `engine_temp` and `wheel_speed` |
| `dtcs.json` | DTC snapshot for `U0100` |
| `generate_trace.py` | Reproducible regenerator (`SEED = 0xC0FFEE91`) |

## Reproducing the trace

```bash
cd examples/u0100_lost_comm/
python3 generate_trace.py > trace.asc
```

## Running

```bash
export ANTHROPIC_API_KEY=sk-...
poetry run diagforge analyze examples/u0100_lost_comm/trace.asc \
    --dtcs examples/u0100_lost_comm/dtcs.json \
    --dbc  examples/u0100_lost_comm/engine_bus.dbc \
    --output ./demo-output/
open demo-output/report.html
```

## What the analyzer should detect

The deterministic Layer 2 analyzer should produce one `notable_finding`
of the form:

> `engine_temp had 4 communication gap(s) within 500ms of the DTC window — gap durations (ms) [260, 299, 331, 391]`

(Exact ms vary by ±1 due to the per-frame jitter; the cluster of four is
deterministic for the committed seed.)

## Expected mitigation match

The leading hypothesis is that the gateway's lost-communication monitor
uses a single-shot threshold that fires on any silence above ~200 ms, and
that adding a small consecutive-misses counter would absorb the brief
buses-level transients. The matching pattern is
`communication_retry_state_machine`, with the recommender computing:

* `timeout_ms` ≈ 30 ms (3 × the 10 ms median publish interval, rounded up).
* `clear_holdoff_ms` ≈ 200 ms (`max(200, 5 × publish interval)`).
* `max_consecutive_misses` = 3 (default; bus is not unusually bursty).
