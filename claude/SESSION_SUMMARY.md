# DiagForge v0.2.0 — Session Summary

Two autonomous Claude Code sessions plus a series of small fix commits
took DiagForge from a zero-state planning directory to a v0.2.0 release.
The first session built **Phase 0-Lite** (T0L.1 → T0L.8) end-to-end. A
short post-autopilot fix series unblocked the live demo, switched to
Anthropic `tool_use`, promoted `claude-opus-4-7` to the default, and
shipped the first computed parameter suggestions. The second autonomous
session added **Phase 0 + Phase 1 (focused subset)**: UDS ingestion,
five more mitigation patterns, two new demo cases (U0100, P0420), inline
timing-diagram SVGs, a Streamlit drag-and-drop UI, multi-DTC correlation,
and a GitHub Action that comments on PRs.

This document is for the human to read before defending the work in
interviews.

## Tickets completed

### Phase 0-Lite (v0.1.0)

| Ticket | Commit | Summary |
|---|---|---|
| docs | `5dc09cf` | Initial planning docs (CLAUDE.md, README, ARCHITECTURE, ROADMAP, schema, mitigation patterns) |
| T0L.1 | `9472ed9` | Project scaffolding (poetry, ruff, mypy strict, pytest, click skeleton) |
| T0L.2 | `4ee43bc` | Pydantic models for ingestion events and the canonical report schema |
| T0L.3 | `b8fa34d` | ASC trace parser, DTC JSON parser, abstract base, fixtures |
| T0L.4 | `1a315b5` | Deterministic timing + value anomaly analyzer (median+MAD baseline) |
| T0L.5 | `18d56d1` | Diagnostic agent with JSON-prefill, evidence validation, single retry (superseded — see ADR-007) |
| T0L.6 | `1967986` | Mitigation pattern library loader + strict-id recommender |
| T0L.7a | `2499247` | Report emitter, sha256 manifest, Jinja2 HTML template |
| T0L.7b | `e70601b` | CLI analyze command wired through all five layers |
| T0L.7c+d | `9779109` | P0300 demo case + integration test, UUIDv7 generator fix |
| T0L.8 | `6575706` | Polish, ADRs, README quick-start update, AI-attribution sweep |
| docs | `6deae89` | Backfill T0L.8 commit hash in session summary |
| fix | `685b0e7` | Upgrade anthropic SDK to resolve httpx proxies kwarg incompatibility |
| fix | `337abe7` | Use tool_use for structured output instead of assistant prefill (supersedes T0L.5 prefill approach) |
| feat | `c03cc20` | Compute concrete mitigation parameters, switch to opus-4-7, simplify HTML report |
| fix | `9d03b21` | Drop deprecated `temperature` param for claude-opus-4-7 compatibility |
| docs | `b999e4b` | Add root README.md for GitHub repo page; remove redundant claude/README.md |
| docs | `e25baca` | Bring SESSION_SUMMARY.md up to v0.1.0 final state |

### Phase 0 + Phase 1 focused subset (v0.2.0)

| Ticket | Commit | Summary |
|---|---|---|
| T0.1 | `b2ef865` | UDS `.log` parser + trace-parser registry; CLI dispatches by extension |
| T0.10 | `70aa3ec` | Expand mitigation library 5 → 10 patterns (comm-retry, hysteresis, freshness, gradient, consensus) |
| T0.4 | `f839851` | U0100 demo case + new `communication_gap` anomaly + comm-retry value computer |
| T0.5 | `a285a49` | P0420 demo + `dematuration_timer` value computer + `--window-ms` CLI + `make demo-all`/`make ui` |
| T0.9 | `7e1ca16` | Inline SVG timing diagrams per DTC in HTML report (matplotlib + Agg backend) |
| T0.8 | `251dfc3` | Streamlit UI: drag-and-drop, live progress, per-DTC cards, bundle download |
| T1.1 | `e2b6e46` | Multi-DTC correlation (co-occurrence + causal ordering) in `cross_dtc_findings` |
| T1.4 | `ce137b6` | GitHub Action for PR diagnostics (poetry cache, change detection, PR comment) |

All commits are conventional-style, ticket-referenced, no AI attribution
anywhere (verified via `grep`).

## Architecture as built

Five deterministic-then-LLM-then-deterministic layers wired through both
a Click `analyze` CLI command and a Streamlit drag-and-drop UI — both
call the same `run_pipeline()` function.

**Ingestion** (Layer 1) now supports two formats: ASC via `python-can`'s
`ASCReader` and `.log` (canutils / candump) via `LogReader`. The
`UdsLogParser` adds lightweight ISO 15765-2 PCI parsing on top of frames
in the UDS request/response range (0x7DF, 0x7E0-0x7EF), surfacing
service ID / subfunction / NRC / first-frame total-length / consecutive-
frame sequence index as decoded signals. Dispatch is by extension via a
small registry in `diagforge/ingestion/registry.py` (ADR-008). Optional
`SignalDecoder` runs the events through `cantools` if a DBC is given, or
synthesises one signal per frame ID otherwise.

**Pattern analysis** (Layer 2) is 100% deterministic and now has four
detectors: value anomalies (median + MAD baseline, ADR-001), transition
anomalies with an analog-skip heuristic (ADR-005), power-rail collapse
bursts, and **communication gaps** (ADR-009). The last is new in v0.2:
on a per-signal basis, any interval exceeding `max(5 × median_interval,
50 ms)` is flagged as a `communication_gap` carrying its duration and
the median publish interval — exactly the data the lost-comm recommender
needs to compute timeouts.

**Multi-DTC correlation** is a separate Layer-2 module
(`diagforge/analyzer/cross_dtc.py`). For every pair of DTCs in the
snapshot it emits one finding: `co_occurring` if the first-seen
timestamps fall within 100 ms of each other, `causal_ordering` if both
DTCs have `occurrence_count > 1` and the earlier DTC's first-seen *and*
last-seen still precede the later DTC's. Findings live in a new
top-level `cross_dtc_findings` block in the report schema; single-DTC
runs remain valid (empty list).

**Diagnostic agent** (Layer 3) wraps Anthropic's SDK behind an
`AnthropicClient` Protocol. The real client declares a single tool
(`submit_diagnostic_result`), forces it via `tool_choice`, returns a
`ToolCallResult(input, resolved_model)` dataclass, and the agent stamps
`DiagnosticResult.model_version` with the API's resolved alias (ADR-007).
No `temperature` kwarg — Claude 4.x rejects it.

**Mitigation recommender** (Layer 4) loads **10 starter patterns** (up
from 5 in v0.1) — the original five plus
`communication_retry_state_machine`, `oscillation_hysteresis`,
`signal_freshness_check`, `gradient_limit_check`, and
`cross_ecu_consensus`. Concrete parameter computation now covers four of
the ten:
* `duration_qualified_debounce.qualification_time_ms` /
  `confirmation_count` from dropout durations,
* `plausibility_check_redundant_signals.tolerance_window_ms` from the
  worst dropout + 20 ms,
* `communication_retry_state_machine.timeout_ms` /
  `clear_holdoff_ms` / `max_consecutive_misses` from publish interval +
  observed gaps,
* `dematuration_timer.dematuration_time_ms` from the median inter-spike
  oscillation period × 5.
The remaining six patterns emit rationale text only (NVM characteristics,
code-side bounds, hysteresis-band measurement etc. are Phase 0+ work).

**Report emitter** (Layer 5) assembles per-DTC analyses into a
UUIDv7-keyed bundle (`report.json`, `report.html`, `manifest.json` with
sha256 hashes). v0.2 adds inline SVG timing diagrams — one per DTC,
showing every decoded signal in the analysis window with vertical
markers at each anomaly event. Rendered via matplotlib with the Agg
backend and embedded inline so the bundle stays self-contained.

**Streamlit UI** (`make ui` / `streamlit run diagforge/ui/app.py`) is
the public face of v0.2. Three drag-and-drop file uploaders, a sidebar
with model + window settings, a live-progress status panel during the
run, expandable per-DTC cards with chart + findings + hypotheses +
mitigation tables, and three download buttons (JSON, HTML, full bundle
.zip). All work lives in `diagforge/ui/pipeline.py` (ADR-010), so the
analysis path is the same code the CLI runs and is unit-testable
without a Streamlit runtime.

**GitHub Action** (`/.github/workflows/diagforge-pr.yml`) triggers on
PRs touching `examples/**` or `traces/**`, caches the poetry venv,
analyses every changed example directory, posts a Markdown summary as a
PR comment, and uploads the full report bundles as an artefact. Setup
guide in `claude/ci-setup.md`.

## Key design decisions made autonomously

Every item below was an active choice; none was dictated by the spec.
ADR numbers reference files in `claude/decisions/`.

* **ADR-001 — Median + MAD baseline (instead of mean + stddev) for value
  anomaly detection.** Real ECU signals are non-Gaussian; outliers drag
  the mean and stddev toward themselves and self-erase.
* **ADR-002 — Strict pattern_id matching, not fuzzy LLM ranking.**
  Equality lookup against the library index; unknown IDs are logged-
  and-dropped rather than raising.
* **ADR-003 — Assistant-side JSON pre-fill for strict structured output**
  (superseded by ADR-007). Claude 4-series rejects assistant-message
  prefill.
* **ADR-004 — Auto-decoder fallback** when `--dbc` is omitted. Emits
  `frame_0x<id_hex>` signals so a reviewer can run DiagForge against any
  ASC without authoring a DBC.
* **ADR-005 — Transition-anomaly detector skips analog signals.** Skip
  any signal whose codomain has > 10 distinct values; otherwise the
  detector spams findings on noisy analog readings.
* **ADR-006 — Makefile neutralizes PYTHONPATH and PYTHONNOUSERSITE.**
  Stops ROS-2 `site-packages` from leaking pytest plugins into the
  poetry venv.
* **ADR-007 — Tool_use with forced tool_choice for structured output.**
  Supersedes ADR-003; carries `ToolCallResult(input, resolved_model)`
  so `model_version` records the API's resolved alias.
* **ADR-008 — Trace parser dispatch via an extension registry.**
  `parser_for(path)` looks up the parser class by extension; adding a
  new format is one import + one list-append in `registry.py`.
* **ADR-009 — Communication-gap detector heuristic.** Threshold =
  `max(5 × median publish interval, 50 ms)`; trades occasional bursty
  contention for clean U-code detection. The recommender parses the gap
  duration and median interval out of the description to compute
  `timeout_ms` and `clear_holdoff_ms`.
* **ADR-010 — Streamlit `pipeline.py` separated from `app.py`.** All
  pipeline logic lives in a Streamlit-free module so the UI is
  unit-testable end-to-end; `app.py` is layout-only and excluded from
  coverage.

Other smaller judgment calls (not their own ADR but worth knowing):

* **UUIDv7 hand-rolled** — 12 lines, no new dependency.
* **`mypy --strict`** with `ignore_missing_imports` only for SDKs
  without type stubs (`can`, `cantools`, `udsoncan`, `matplotlib`,
  `streamlit`).
* **Ruff replaces flake8 + isort + pyupgrade + bugbear**, with
  `RUF001/002/003` globally ignored so `×`, `→`, `±`, `µ` flow freely
  in automotive copy.
* **`pytest --cov=diagforge` always on** via `addopts`.
* **`ANTHROPIC_API_KEY` checked in the CLI itself**, not just the SDK
  wrapper.
* **`--model` and `--window-ms` are CLI flags.** Default model
  `claude-opus-4-7`, default window 500 ms; both demos that need slow-
  signal coverage (U0100, P0420) pass `--window-ms 1500`.
* **Bytes payloads kept as `bytes`** through the model boundary;
  pydantic v2 round-trips them as base64.
* **`Report` model uses `extra="forbid"`** at every layer.
* **`DtcAnalysis.dtc` uses `DtcInfo`** (report-facing), not
  `DTCSnapshot` (ingestion-facing). The CLI bridges them.
* **`prompt_hash` recorded on every DiagnosticResult** for
  reproducibility-debugging.
* **HTML uses inline CSS, no JS, no fonts.** Provenance footer
  intentionally removed; lives in `report.json` only.
* **Per-dropout / per-gap duration is parsed from the anomaly
  description text via small regexes.** Quick and ugly; works; tests
  pin the description format.
* **Property-based tests (hypothesis) only on the statistics
  functions** — full-builder property tests would mostly assert
  "doesn't crash."
* **The mitigation library loader supports multi-file merging**, so
  third parties can drop YAML into `diagforge/mitigation/data/` without
  touching loader code.
* **Mitigation parameter dispatch via a small `_DISPATCH` table.**
  Adding a value-computer for the next pattern is one dict entry + one
  `_suggest_<pattern>` function.
* **Integration tests mock via `monkeypatch.setattr` on the CLI
  module's `RealAnthropicClient` symbol** — lightest-weight DI.
* **Synthetic-trace seeds are committed** (`0xD1A9F07E` for P0300,
  `0xC0FFEE91` for U0100, `0xCA7A1FA11` for P0420) so the traces
  round-trip byte-for-byte.
* **Each P0300 dropout is 3 frames wide with 3-5 idle frames between**;
  each U0100 dropout is 250-380 ms wide on the 100 Hz publisher; each
  P0420 spike is 80-100 ms wide at irregular 250-350 ms spacing.
* **Inline SVG charts use thin lines, no markers, palette-cycle
  colours**, with anomaly events as thin vertical warning-colour lines
  and the DTC window highlighted as a translucent band. Renders under
  ~10 KB per chart.
* **The Streamlit UI's `_FakeAnthropicClient` test fixture extracts
  any quoted finding** matching `dropped|spiked|silent|gap` from the
  user prompt — works for every trace shape the project supports.
* **Cross-DTC `causal_ordering` requires a two-endpoint proxy** (both
  first and last lags positive) because the DTC snapshot lacks per-
  occurrence timestamps. A per-occurrence-array schema extension is
  Phase 2.
* **`gh pr comment --body-file -` for the GitHub Action**, posting a
  fresh comment per run rather than chasing an existing comment ID.
  Cleaner workflow YAML, slightly more PR noise (acceptable).
* **`make demo-all` runs all three demo cases sequentially**, each
  writing to its own subdir under `demo-output/` (no clobbering).

## How to run

```bash
export ANTHROPIC_API_KEY=sk-...

make demo         # P0300 intermittent misfire
make demo-u0100   # U0100 lost communication
make demo-p0420   # P0420 catalyst threshold
make demo-all     # all three back-to-back

make ui           # Streamlit drag-and-drop UI on localhost:8501
```

## Where the report is

Each demo writes to `demo-output/<slug>/`:

```
demo-output/p0300/
├── report.json     (canonical structured report; validates against claude/report-schema.json)
├── report.html     (Jinja2-rendered summary with inline SVG timing chart per DTC)
└── manifest.json   (sha256 hashes + file sizes for the bundle)
```

## Coverage report

Final from `make test` after T1.4:

```
146 passed, total coverage 90% (branch + line)
diagforge/_logging.py                       100%
diagforge/analyzer/cross_dtc.py             100%
diagforge/analyzer/timing.py                 79%   ← uncovered: helper edge branches
diagforge/cli.py                            100%
diagforge/diagnostic/agent.py                84%   ← uncovered: RealAnthropicClient (live SDK)
diagforge/diagnostic/prompts.py             100%
diagforge/ingestion/base.py                 100%
diagforge/ingestion/can_asc.py               83%
diagforge/ingestion/dtc_json.py              96%
diagforge/ingestion/models.py               100%
diagforge/ingestion/registry.py             100%
diagforge/ingestion/signal_decode.py         92%
diagforge/ingestion/uds.py                   88%
diagforge/mitigation/library.py              93%
diagforge/mitigation/recommender.py          94%
diagforge/report/charts.py                   90%
diagforge/report/emitter.py                  98%
diagforge/report/html.py                     80%
diagforge/report/models.py                   97%
diagforge/ui/pipeline.py                     97%
```

`diagforge/ui/app.py` is excluded from coverage (Streamlit script-mode,
loadable only via `streamlit run`); all UI logic lives in `pipeline.py`.

## Known limitations / TODOs

Done since v0.1: UDS ingestion, two more demo cases (U0100, P0420),
ten mitigation patterns total, four pattern value-computers wired
through `_DISPATCH`, inline SVG timing diagrams, Streamlit drag-and-drop
UI, multi-DTC correlation, GitHub Action for PRs.

Still cut from v0.2 (deferred):

* **No BLF or J1939 ingestion** — Phase 0 ticket T0.3 (BLF) and an
  unscoped J1939 ticket.
* **No multi-channel CAN handling** — currently we treat channel as a
  hint, not a partition key (Phase 1 ticket T1.2).
* **Cross-DTC `causal_ordering` proxy is two-endpoint.** Per-
  occurrence-array support requires extending the DTC snapshot schema
  (Phase 2).
* **`correlations` field in PatternFeatures is still always empty.**
  Cross-signal lag-correlation is an open Phase 1 task.
* **Six of ten mitigation patterns still emit rationale-only.**
  `oscillation_hysteresis` needs noise-band measurement;
  `signal_freshness_check` needs per-signal cycle-time hints;
  `gradient_limit_check` needs signal-physics metadata;
  `cross_ecu_consensus` needs cross-signal redundancy detection;
  `retry_state_machine_nvm` needs NVM device characteristics;
  `boundary_condition_guard` needs code-side bounds metadata.
* **Auto-decoder collapses multi-signal frames** to one signal per
  frame ID. DBC mode is the supported workflow.
* **No DBC-derived "discrete vs analog" hint** — ADR-005 uses a value-
  count heuristic instead.
* **Per-dropout / per-gap duration is regex-parsed** from anomaly
  descriptions. Brittle if the description format changes; a structured
  `evidence.duration_us` field on the anomaly model is a Phase 2 fix.
* **Confidence calibration not implemented** — Phase 1 ticket T1.3
  (compare model `confidence` against empirical accuracy).
* **ML-based anomaly detection, active-query mode, vcan integration,
  RAG over standards** — Phase 2 stretch goals, unchanged.

## Release status

The v0.2.0 codebase is on `main` at GitHub
(`https://github.com/satyanar-lab/DiagForge`) and ready to push. Suggested
tagging once you've personally verified `make demo-all` and `make ui`
against the live API and reviewed the HTML reports:

```bash
git push                                              # all v0.2.0 commits to origin/main
git tag -a v0.2.0 -m "v0.2.0 — Phase 0 demos, Streamlit UI, multi-DTC, GitHub Action"
git push origin v0.2.0
```
