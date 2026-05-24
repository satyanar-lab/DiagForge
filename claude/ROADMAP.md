# DiagForge Roadmap

Each ticket below is sized for **one Claude Code session**. The user will give
you the ticket prompt one at a time. After completing a ticket, run
`make lint && make test`, then suggest a commit message (without AI attribution)
and wait for the user to commit.

---

## Phase 0-Lite — Weekend MVP (target: 1 focused weekend, 8–15 hours)

**Goal:** A working CLI that ingests an ASCII CAN log + a DTC snapshot file,
extracts timing and value-anomaly patterns around DTC occurrences, calls Claude
for ranked hypotheses, matches a mitigation pattern, and emits a JSON + HTML
report. One end-to-end demo case (the P0300 intermittent misfire scenario)
ships with it.

### T0L.1 — Project scaffolding
Initialize the Python project per `claude/CLAUDE.md` structure:
`pyproject.toml` (Poetry), `Makefile`, `.gitignore` additions (Python-specific
lines beyond what's already there), `LICENSE` (MIT), `diagforge/__init__.py`
with `__version__ = "0.0.1"`, empty `diagforge/cli.py` with a click skeleton,
`diagforge/_logging.py` with a `get_logger(name)` helper, `tests/` with one
dummy passing test. Configure ruff + black + mypy (strict) + pytest. Phase
0-Lite dependencies only — no Streamlit, no udsoncan, but include click,
pydantic v2, python-can, cantools, anthropic, pyyaml, jinja2.

**Acceptance:** `make install`, `make lint`, `make test` all green.

---

### T0L.2 — Report schema + pydantic models
Read `claude/report-schema.json`. Implement `diagforge/report/models.py` with
full pydantic v2 models matching every field. Implement
`diagforge/ingestion/models.py` with `TraceEvent` and `DTCSnapshot` per
CLAUDE.md. Round-trip and validation tests in `tests/unit/`.

**Acceptance:** Schema → model → JSON → model round-trip passes; invalid input
fails with useful errors; mypy strict clean.

---

### T0L.3 — Trace ingestion: ASC parser + DTC JSON parser
Implement `diagforge/ingestion/base.py` (abstract `TraceParser` and
`DtcParser`), `diagforge/ingestion/can_asc.py` using `python-can`'s ASCReader,
and `diagforge/ingestion/dtc_json.py` for a JSON DTC snapshot file. Document
the DTC JSON shape in `claude/dtc-input-format.md`. Add sample fixtures in
`tests/fixtures/` and unit tests covering happy path + 3 failure modes each.

**Acceptance:** Parses the sample fixture; gracefully errors on malformed
input; tests cover the failure modes.

---

### T0L.4 — Pattern analyzer: timing + value anomaly detection
Implement `diagforge/analyzer/timing.py` with these public functions, all
pydantic-typed:
- `compute_signal_summaries(events, signals_of_interest, window_us)`
- `detect_transition_anomalies(events, signal_name, window_us=50_000, transition_threshold=5)`
- `detect_value_anomalies(events, signal_name, window_us=200_000, deviation_sigmas=3.0, min_anomaly_duration_us=5_000)` — use median + MAD as the baseline since real signals are non-Gaussian
- `detect_power_cycle_bursts(events, supply_signal_name, window_us=200_000)`
- `build_pattern_features(events, dtc_snapshot, window_us=500_000)` — top-level
  builder; must populate `notable_findings` with specific numeric strings.

All functions are deterministic. Property-based tests via hypothesis for the
statistics functions. Notable findings must use real numbers, e.g.:
`"engine_rpm dropped from ~800 RPM to <100 RPM in 4 occurrences within 200ms (durations: 28ms, 31ms, 29ms, 33ms)"`

**Acceptance:** Tests pass; on the P0300 demo fixture, the analyzer correctly
flags 4 value dropouts in the analysis window and reports their durations and
depths.

---

### T0L.5 — Diagnostic agent: Claude with structured output
Implement `diagforge/diagnostic/prompts.py` (with `PROMPT_TEMPLATE_VERSION = "diag-v1"`
and a `build_diagnostic_prompt` function) and `diagforge/diagnostic/agent.py`
(with `DiagnosticAgent` class). Use `claude-sonnet-4-6` with strict JSON-mode
output. Validate response with pydantic. Each hypothesis must cite an item
from `notable_findings` — if not, retry ONCE with feedback in the prompt,
then raise `EvidenceMissingError`. On parse failure, log raw (truncated 500
chars) and raise `DiagnosticParseError`. Mock the Anthropic client in all
unit tests.

**Acceptance:** Tests cover happy path, malformed response, missing-evidence
retry, timeout, all-invalid-pattern-ids. Prompt template version-tagged.

---

### T0L.6 — Mitigation library loader + recommender
Implement `diagforge/mitigation/library.py` (loads pattern YAML files from
`diagforge/mitigation/data/` — copy `claude/mitigation-patterns-starter.yaml`
to that location at the start of this ticket as `diagforge/mitigation/data/starter.yaml`).
Pattern pydantic model mirrors one YAML entry. `MitigationLibrary` exposes
`get_by_id(pattern_id)` and `list_pattern_ids()`. Then implement
`diagforge/mitigation/recommender.py` with `MitigationRecommender.match(
diagnostic_result, features)` returning `list[MitigationMatch]`.

For each hypothesis with a non-null `suggested_pattern_id`: look up the
pattern, emit `MitigationMatch` with parameter suggestions (initially just
the suggestion_rule text as rationale; leave value=null where computation
isn't implemented).

**Acceptance:** All 5 starter patterns load; lookup-by-id works; unknown id
returns None (not error); matching uses hypotheses correctly.

---

### T0L.7 — Report emitter + end-to-end CLI + P0300 demo
The end-to-end ticket. Likely split across two sittings.

**Part A — Report emitter:** `diagforge/report/emitter.py` (`ReportEmitter`
class with `start_run`, `add_analysis`, `finalize` — writes `report.json`
matching the schema and `manifest.json` with sha256 hashes).
`diagforge/report/html.py` with one Jinja2 template
(`diagforge/report/templates/report.html`) — simple, inline CSS, no JS.

**Part B — CLI wiring:** Fill in `diagforge/cli.py analyze TRACE_PATH
--dtcs DTC_PATH --output OUT_DIR [--dbc DBC_PATH] [--verbose]`. Wire the
full pipeline.

**Part C — P0300 intermittent misfire demo:**
- `examples/p0300_intermittent_misfire/trace.asc` — synthetic ASC log,
  CAN ID 0x100 carrying `engine_rpm` (uint16, scale 0.25 RPM/bit), broadcast
  every ~10ms. ~50 frames at idle (~800 RPM), then 4 brief dropouts (single
  frames with RPM < 100) at irregular intervals within a 200ms window, then
  back to idle. **Irregular intervals, varied dropout depths, ±10 RPM idle
  jitter — it must look realistic, not toy-ish.**
- `examples/p0300_intermittent_misfire/dtcs.json` — one DTC: code "P0300",
  standard "obd2", description "Random/Multiple Cylinder Misfire Detected".
- `examples/p0300_intermittent_misfire/README.md` — scenario writeup.
- Optional: `generate_trace.py` script with fixed RNG seed for reproducibility.

**Part D — Integration test:** `tests/integration/test_p0300_demo.py` invokes
the CLI with mocked Anthropic returning a canned response citing the dropout
finding and suggesting `dematuration_timer`. Asserts the output JSON has the
expected structure.

**Part E — Makefile demo target:** `make demo` runs the analyze command on
the P0300 example against the real Anthropic API.

**Acceptance:** `diagforge analyze examples/p0300_intermittent_misfire/trace.asc
--dtcs examples/p0300_intermittent_misfire/dtcs.json --output /tmp/r` produces
`/tmp/r/report.json` and `/tmp/r/report.html`. Integration test passes.
`make demo` works if API key is set.

Suggested commit messages (split into logical commits):
- `T0L.7a: report emitter and HTML template`
- `T0L.7b: CLI analyze command wiring`
- `T0L.7c: P0300 intermittent misfire demo case`
- `T0L.7d: integration test for P0300 demo`

---

### T0L.8 — Polish and ship v0.1.0
Update README's Quick Start to match working commands. Add a Demo section
with a screenshot placeholder. Write ADRs in `claude/decisions/` for any
non-obvious choices made in T0L.1–T0L.7. Run `make lint && make test` final
time. **Search the repo for any AI attribution** (`grep -ri "Claude\|Anthropic\|AI-generated\|Co-authored"` — anything except `anthropic` SDK references is a violation). Suggest a final commit message and `git tag v0.1.0` command.
Do not commit or tag yourself.

---

## Phase 0 — Full MVP (2–3 weekends after Phase 0-Lite)

- T0.1 — UDS ingestion via `udsoncan`
- T0.2 — OBD-II PID + Mode 04/09/19 support
- T0.3 — BLF (Vector Binary Logging) format support
- T0.4 — Demo case 2: U0100 lost communication
- T0.5 — Demo case 3: P0420 catalyst threshold (dematuration)
- T0.6 — Demo case 4: NVM data loss (retry state machine)
- T0.7 — Demo case 5: boundary-condition (plausibility check + bounds guard)
- T0.8 — Streamlit UI with file upload and live progress
- T0.9 — Timing-diagram rendering in HTML report (matplotlib → embedded SVG)
- T0.10 — Expand mitigation library to 10+ patterns

---

## Phase 1 — Multi-DTC + multi-ECU analysis (3–4 weeks)

- T1.1 — Multi-DTC correlation (co-occurring DTCs, causal ordering)
- T1.2 — Multi-channel CAN trace handling (multiple buses in one log)
- T1.3 — Confidence calibration: hypothesis confidence vs. empirical accuracy
- T1.4 — GitHub Action: run on PR-attached trace logs, comment results

---

## Phase 2 — Stretch goals

- T2.1 — ML-based anomaly detection (isolation forest on signal feature space)
- T2.2 — Active-query mode: agent requests additional data when uncertain
- T2.3 — Integration with virtual CAN bus (vcan / SocketCAN) for replay
- T2.4 — RAG over public standards summaries

---

## Working-with-Claude-Code playbook

For each ticket the user pastes:

1. Re-read `claude/CLAUDE.md` in case context was compacted.
2. Read the ticket carefully. Read any code files already in the relevant area.
3. **Propose a plan before writing code.** Wait for user approval.
4. Implement. Write code in small, reviewable chunks.
5. Run `make lint && make test`. Fix any issues.
6. Summarize what changed in 3–5 bullets.
7. List any TODOs or assumptions you introduced.
8. Write an ADR in `claude/decisions/` if a non-obvious choice was made.
9. Suggest a commit message of the form `T0L.X: <summary>` — never with AI attribution.
10. Wait for the user to commit. Do not commit yourself.
