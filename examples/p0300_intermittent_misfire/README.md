# Demo case — P0300 random/multiple cylinder misfire detected

This case reproduces the most common shape of an intermittent misfire complaint
from the OBD-II side: a brief sequence of crankshaft-speed sags that the misfire
detector aggregates into a `P0300` set, followed by an idle that looks
perfectly healthy.

## Scenario

* 100 Hz `engine_rpm` broadcast on CAN ID `0x100` from the ECM
  (`engine.dbc` carries the signal definition: 16-bit little-endian unsigned,
  0.25 RPM/bit).
* About 500 ms of clean idle around 800 RPM with ±10 RPM noise.
* Four engine-speed dropouts in a ~300 ms window — durations near 30 ms each,
  varied depths (~50, ~95, ~70, ~130 RPM), with 3–5 idle frames in between.
* Clean idle resumes after the cluster.
* `dtcs.json` carries the single `P0300` set, anchored at the first dropout.

## Files

| File | Purpose |
|---|---|
| `trace.asc` | Synthetic ASC CAN log (95 frames, ~960 ms). |
| `engine.dbc` | DBC with the `engine_rpm` signal definition. |
| `dtcs.json` | DTC snapshot for `P0300`. |
| `generate_trace.py` | Reproducible regenerator (`SEED = 0xD1A9F07E`). |

## Reproducing the trace

```bash
cd examples/p0300_intermittent_misfire/
python3 generate_trace.py > trace.asc
```

The generator uses a fixed `random.Random` seed; bit-for-bit reproducibility is
asserted by the integration test.

## Running

```bash
export ANTHROPIC_API_KEY=sk-...
poetry run diagforge analyze examples/p0300_intermittent_misfire/trace.asc \
    --dtcs examples/p0300_intermittent_misfire/dtcs.json \
    --dbc  examples/p0300_intermittent_misfire/engine.dbc \
    --output ./demo-output/
open demo-output/report.html
```

## What the analyzer should detect

The deterministic Layer 2 analyzer should produce one `notable_finding` of the
form:

> `engine_rpm dropped 4 time(s) within 500ms of the DTC window — extreme values [48, 68, 89, 131], durations (ms) [30, 30, 30, 29]`

This is the only ground truth Layer 3 (the LLM) is allowed to cite as evidence.

## Expected mitigation match

The most likely hypothesis is that the misfire detector is under-debounced
against transient crank-speed sags — i.e. fault qualification fires before a
dematuration timer has had a chance to suppress a short sag. The matching
mitigation pattern is `dematuration_timer`, with
`dematuration_time_ms` suggested at roughly 5× the dominant dropout duration.
