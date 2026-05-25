# Demo case — P0420 Catalyst System Efficiency Below Threshold (Bank 1)

This case reproduces the most common shape of a P0420 complaint: the
post-catalyst O2 sensor signal mirroring the upstream rich/lean switching
that a healthy catalyst would absorb. The intermittent crossings of the
"cat-inefficient" voltage threshold set and clear P0420 repeatedly until
the count exceeds the OEM aging threshold.

## Scenario

* `o2_voltage_b1s2` broadcast on CAN ID `0x401` from the ECM at 50 Hz
  (`o2.dbc` defines a 16-bit little-endian unsigned signal, 0.001 V/bit).
* Healthy baseline ~0.12 V (lean, post-cat should be quiet).
* Eight rich-side excursions to ~0.85 V at irregular spacing
  (~300 ms inter-event), each ~80–100 ms wide.
* DTC `P0420` anchored at the third excursion (`occurrence_count = 3`)
  and persisting through the eighth.

## Files

| File | Purpose |
|---|---|
| `trace.asc` | Synthetic ASC CAN log, ~135 frames, ~2.7 s duration |
| `o2.dbc` | DBC defining `o2_voltage_b1s2` |
| `dtcs.json` | DTC snapshot for `P0420` |
| `generate_trace.py` | Reproducible regenerator (`SEED = 0xCA7A1FA11`) |

## Reproducing the trace

```bash
cd examples/p0420_catalyst_threshold/
python3 generate_trace.py > trace.asc
```

## Running

```bash
export ANTHROPIC_API_KEY=sk-...
poetry run diagforge analyze examples/p0420_catalyst_threshold/trace.asc \
    --dtcs examples/p0420_catalyst_threshold/dtcs.json \
    --dbc  examples/p0420_catalyst_threshold/o2.dbc \
    --output ./demo-output/
open demo-output/report.html
```

## What the analyzer should detect

A single notable finding of the form:

> `o2_voltage_b1s2 spiked 8 time(s) within 2000ms of the DTC window — peak values [~0.85V], durations (ms) [~80-100]`

(Exact peak voltages and ms vary by ±a few percent due to the per-sample
noise model; the cluster of eight is deterministic for the committed seed.)

## Expected mitigation match

The leading hypothesis is that the P0420 evaluator uses a single-shot
threshold that latches on each rich excursion, and that a dematuration
timer five times the dominant oscillation period would suppress the
set/clear chatter. The matching pattern is `dematuration_timer`, with the
recommender computing:

* `dematuration_time_ms` ≈ 1500 ms (5 × the ~300 ms median inter-spike
  interval, rounded up to the nearest 50 ms).
* `clear_threshold_offset` — rationale only (requires hysteresis-band
  measurement the v0.2 analyzer doesn't yet derive).
