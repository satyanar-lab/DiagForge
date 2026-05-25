# DiagForge

> **An open-source root-cause co-pilot for vehicle diagnostic trouble codes.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Standards](https://img.shields.io/badge/standards-UDS%20%7C%20OBD--II%20%7C%20J1939%20%7C%20CAN-orange.svg)](claude/ARCHITECTURE.md)

**Status:** v0.1.0 — Phase 0-Lite shipped, Phase 0 in progress.

---

## What it does

DiagForge takes a captured vehicle diagnostic trace (CAN, CAN-FD, UDS, or
OBD-II logs) and a list of observed DTCs, then:

1. **Extracts timing and correlation patterns** around each DTC occurrence —
   transition rates, debounce candidates, signal stability windows, NVM
   write/power-cycle relationships.
2. **Proposes ranked root-cause hypotheses** with explicit evidence — using
   an LLM grounded in the pattern statistics, ISO 14229, ISO 15031, and J1939.
3. **Recommends mitigation patterns** from a curated library — with
   **concrete parameter values computed from the observed signal evidence**
   (e.g. `qualification_time_ms = max(dropout_durations) × 2`), not just
   rationale text.
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
│  3. Diagnostic Agent (LLM)   │   ranked hypotheses + evidence,
│    (claude-opus-4-7)         │   strict structured output
└──────────────────────────────┘
│  hypotheses
▼
┌──────────────────────────────┐
│  4. Mitigation Recommender   │   matches hypotheses → patterns
│                              │   + computed parameter values
└──────────────────────────────┘
│  patterns + verification approach
▼
┌──────────────────────────────┐
│  5. Evidence Report Emitter  │   JSON + HTML + sha256 manifest
└──────────────────────────────┘
│
▼
[ audit-bundle/ ]

Layer 3 uses the Anthropic `claude-opus-4-7` model (fallback
`claude-sonnet-4-6`) and constrains it to a structured-output schema so it
cannot fabricate fields or numbers — every hypothesis must cite an
analyzer-produced `notable_findings` string verbatim, or the call is
retried once with feedback and then fails with `EvidenceMissingError`.
Detailed design: [`claude/ARCHITECTURE.md`](claude/ARCHITECTURE.md).

Design decisions and rationale: see
[`claude/SESSION_SUMMARY.md`](claude/SESSION_SUMMARY.md).

## Quick start

```bash
git clone https://github.com/satyanar-lab/DiagForge.git
cd DiagForge
make install                   # poetry install + dep check
export ANTHROPIC_API_KEY=sk-... # required: read at run time, never committed

# Analyze the P0300 intermittent misfire demo case
poetry run diagforge analyze \
  examples/p0300_intermittent_misfire/trace.asc \
  --dtcs examples/p0300_intermittent_misfire/dtcs.json \
  --dbc  examples/p0300_intermittent_misfire/engine.dbc \
  --output ./demo-output/

open demo-output/report.html   # macOS;  xdg-open on Linux
```

Or use the bundled `make demo` target (P0300 example):

```bash
make demo                       # P0300 intermittent misfire
make demo-u0100                 # U0100 lost communication
make demo-p0420                 # P0420 catalyst threshold
make demo-all                   # all three back-to-back
```

Or use the web UI — drag-and-drop a trace, DTC snapshot, and optional DBC:

```bash
make ui                         # launches Streamlit on localhost:8501
```

### Demo output

`demo-output/` is a self-contained audit bundle:

- **`report.json`** — canonical structured report, conforming to
  [`claude/report-schema.json`](claude/report-schema.json) and validated through
  pydantic v2 on emit. Source of truth for tooling integrations.
- **`report.html`** — Jinja2-rendered human-readable summary with inline CSS
  and no JavaScript; opens in any browser or email-archive viewer.
- **`manifest.json`** — file index with sha256 hashes and byte sizes for
  every artefact in the bundle, anchored to a UUIDv7 `report_id`.

## Mitigation pattern library

DiagForge ships with a curated library of **10 fault-handling patterns**
commonly applied to false-positive DTCs in production ECU software. A
subset derive concrete numeric parameter suggestions directly from the
deterministic analyzer's findings; the rest emit parameter rationale text
with values pending a Phase 0+ value-computer per pattern.

| Pattern | When it applies | Computed values |
|---|---|---|
| Duration-qualified debounce | Signal toggles within the noise window of a discrete input | `qualification_time_ms` = `max(dropout_durations) × 2`, rounded up to nearest 5 ms; `confirmation_count` = 1 |
| Plausibility check (redundant signals) | Mismatch between redundant signals (e.g. switch + sensor) | `tolerance_window_ms` = `max(dropout_durations) + 20` |
| Dematuration timer | Analog fault qualifies and clears repeatedly before fault confirmation | `dematuration_time_ms` ≈ 5 × dominant oscillation period (T0.5 demo) |
| Communication retry state machine | Lost-comm U-codes from brief bus dropouts | `timeout_ms`, `max_consecutive_misses` from observed gaps (T0.4 demo) |
| Oscillation hysteresis | Signal chatters across a single threshold; dematuration alone insufficient | rationale only |
| Signal freshness / timeout check | Stale received signal feeding safety logic | rationale only |
| Gradient / rate-of-change limit | Physically-impossible single-sample jumps | rationale only |
| Cross-ECU consensus / voting | Redundant publishers disagree | rationale only |
| Retry state machine w/ NVM persistence | Data loss across power cycles or transient NVM errors | rationale only |
| Boundary-condition guard | Off-by-one or array-OOB symptoms in fault data | rationale only |

Each pattern is parameterised, verifiable, and cites a public ISO/SAE clause — see
[`claude/mitigation-patterns-starter.yaml`](claude/mitigation-patterns-starter.yaml).

## Standards referenced

- **ISO 14229-1** — Unified Diagnostic Services (UDS)
- **ISO 15031-5** — OBD-II emissions-related diagnostic services
- **ISO 15765** — Diagnostic communication over CAN
- **SAE J1939-73** — Heavy-duty vehicle diagnostics
- **ISO 11898** — CAN frame format

DiagForge is a developer tool and does **not** replace certified workshop
diagnostic equipment or OEM scan tools.

## Roadmap

See [`claude/ROADMAP.md`](claude/ROADMAP.md). Summary:

- **Phase 0-Lite (v0.1.0, shipped)** — CLI MVP with ASC parser, P0300 misfire demo case end-to-end, opus-4-7 structured output, computed mitigation parameters for two starter patterns.
- **Phase 0 (in progress)** — UDS + OBD-II support, BLF format, all 5 demo cases, full computed-parameter coverage, Streamlit UI.
- **Phase 1** — Multi-DTC correlation, multi-ECU analysis, HTML reports with timing diagrams.
- **Phase 2** — GitHub Action, ML-based anomaly detection, expanded pattern library.

## License

- Source code: MIT
- Mitigation pattern library (`mitigation-patterns-*.yaml`): CC-BY-4.0
