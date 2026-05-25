# DiagForge

> **An open-source root-cause co-pilot for vehicle diagnostic trouble codes.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Standards](https://img.shields.io/badge/standards-UDS%20%7C%20OBD--II%20%7C%20J1939%20%7C%20CAN-orange.svg)](ARCHITECTURE.md)

---

## What it does

DiagForge takes a captured vehicle diagnostic trace (CAN, CAN-FD, UDS, or
OBD-II logs) and a list of observed DTCs, then:

1. **Extracts timing and correlation patterns** around each DTC occurrence —
   transition rates, debounce candidates, signal stability windows, NVM
   write/power-cycle relationships.
2. **Proposes ranked root-cause hypotheses** with explicit evidence — using
   an LLM grounded in the pattern statistics, ISO 14229, ISO 15031, and J1939.
3. **Recommends mitigation patterns** from a curated library — duration-qualified
   debounce, dematuration timers, retry state machines with NVM persistence,
   plausibility checks, and signal qualification strategies.
4. **Emits an evidence report** — JSON for tooling integration, HTML for review.

It gives an embedded software engineer the first 80% of a DTC root cause
analysis in 30 seconds, with the reasoning visible enough to defend in code
review.

## The problem

Field-service engineers, ECU developers, and integration testers spend a
disproportionate share of their time on DTC analysis — sifting through CAN
traces, correlating signal transitions, comparing against service procedures,
and pattern-matching against past incidents. The tooling is largely
OEM-proprietary, expensive, or both. Open-source automotive diagnostic
analysis is thin, and AI-assisted variants barely exist.

Most false-positive faults in production ECU software fall into a small set
of recurring patterns: insufficient debounce, missing dematuration, NVM
update races, plausibility gaps between redundant signals, and boundary
conditions on ADC/PWM channels. DiagForge codifies these patterns and
matches them against observed trace data automatically.

## Architecture
[ CAN/CAN-FD/UDS log + DTC snapshot + (optional) DBC ]
│
▼
┌──────────────────────────────┐
│  1. Trace Ingestion          │   python-can, cantools, udsoncan
└──────────────────────────────┘
│  normalized events
▼
┌──────────────────────────────┐
│  2. Pattern Analyzer         │   timing stats, correlation,
│    (deterministic)           │   anomaly detection
└──────────────────────────────┘
│  pattern features
▼
┌──────────────────────────────┐
│  3. Diagnostic Agent (LLM)   │   ranked hypotheses + evidence
└──────────────────────────────┘
│  hypotheses
▼
┌──────────────────────────────┐
│  4. Mitigation Recommender   │   matches hypotheses → patterns
└──────────────────────────────┘
│  patterns + verification approach
▼
┌──────────────────────────────┐
│  5. Evidence Report Emitter  │   JSON + HTML
└──────────────────────────────┘
│
▼
[ audit-bundle/ ]

Detailed design: [`ARCHITECTURE.md`](ARCHITECTURE.md)

## Quick start

```bash
git clone https://github.com/<you>/DiagForge
cd DiagForge
make install                   # poetry install + dep check
export ANTHROPIC_API_KEY=sk-... # required: read at run time, never committed

# Analyze the P0300 intermittent misfire demo case
poetry run diagforge analyze \
  examples/p0300_intermittent_misfire/trace.asc \
  --dtcs examples/p0300_intermittent_misfire/dtcs.json \
  --dbc  examples/p0300_intermittent_misfire/engine.dbc \
  --output ./demo-output/

open demo-output/report.html
```

Or use the bundled `make demo` target:

```bash
make demo                       # runs the P0300 example end-to-end
```

## Demo

`make demo` ingests the P0300 trace, runs the deterministic analyzer over a
500 ms window around the DTC, asks Claude to rank root-cause hypotheses
strictly citing the analyzer findings, and emits a self-contained HTML +
JSON report bundle. Typical run time is under 10 seconds.

A screenshot of the rendered HTML report belongs here — capture
`demo-output/report.html` after running `make demo`.

## Mitigation pattern library

DiagForge ships with a curated library of fault-handling patterns commonly
applied to false-positive DTCs in production ECU software:

| Pattern | When it applies |
|---|---|
| Duration-qualified debounce | Signal toggles within the noise window of a discrete input |
| Dematuration timer | Analog fault qualifies and clears repeatedly before fault confirmation |
| Retry state machine w/ NVM persistence | Data loss across power cycles or transient NVM errors |
| Plausibility check | Mismatch between redundant signals (e.g. switch + sensor) |
| Boundary-condition guard | Off-by-one or array-OOB symptoms in fault data |

Each pattern is parameterized and citable — see
[`mitigation-patterns-starter.yaml`](mitigation-patterns-starter.yaml).

## Standards referenced

- **ISO 14229-1** — Unified Diagnostic Services (UDS)
- **ISO 15031-5** — OBD-II emissions-related diagnostic services
- **ISO 15765** — Diagnostic communication over CAN
- **SAE J1939-73** — Heavy-duty vehicle diagnostics
- **ISO 11898** — CAN frame format

DiagForge is a developer tool and does **not** replace certified workshop
diagnostic equipment or OEM scan tools.

## Roadmap

See [`ROADMAP.md`](ROADMAP.md). Summary:

- **Phase 0-Lite** — CLI MVP with ASC parser, P0300 misfire demo case end-to-end
- **Phase 0** — UDS + OBD-II support, all 5 demo cases, full mitigation library
- **Phase 1** — Multi-DTC correlation, multi-ECU analysis, Streamlit UI, HTML reports with timing diagrams
- **Phase 2** — GitHub Action, ML-based anomaly detection, expanded pattern library

## License

- Source code: MIT
- Mitigation pattern library (`mitigation-patterns-*.yaml`): CC-BY-4.0
