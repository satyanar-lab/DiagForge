# DiagForge Phase 0-Lite — Session Summary

A single autonomous Claude Code session built all of Phase 0-Lite end-to-end
(tickets T0L.1 through T0L.8). This document is for the human to read before
defending the work in interviews.

## Tickets completed

| Ticket | Commit | Summary |
|---|---|---|
| docs | `5dc09cf` | Initial planning docs (CLAUDE.md, README, ARCHITECTURE, ROADMAP, schema, mitigation patterns) |
| T0L.1 | `9472ed9` | Project scaffolding (poetry, ruff, mypy strict, pytest, click skeleton) |
| T0L.2 | `4ee43bc` | Pydantic models for ingestion events and the canonical report schema |
| T0L.3 | `b8fa34d` | ASC trace parser, DTC JSON parser, abstract base, fixtures |
| T0L.4 | `1a315b5` | Deterministic timing + value anomaly analyzer (median+MAD baseline) |
| T0L.5 | `18d56d1` | Diagnostic agent with JSON-prefill, evidence validation, single retry |
| T0L.6 | `1967986` | Mitigation pattern library loader + strict-id recommender |
| T0L.7a | `2499247` | Report emitter, sha256 manifest, Jinja2 HTML template |
| T0L.7b | `e70601b` | CLI analyze command wired through all five layers |
| T0L.7c+d | `9779109` | P0300 demo case + integration test, UUIDv7 generator fix |
| T0L.8 | `6575706` | Polish, ADRs, README quick-start update, AI-attribution sweep |

All commits are conventional-style, ticket-referenced, no AI attribution
anywhere (verified via `grep`).

## Architecture as built

Five deterministic-then-LLM-then-deterministic layers wired through a
single Click `analyze` command. **Ingestion** uses `python-can`'s `ASCReader`
to lift CAN frames out of an ASC log, then runs them through a
`SignalDecoder` that either applies a user-supplied DBC (via `cantools`) or
synthesises one signal per frame ID from raw payload bytes. **Pattern
analysis** is 100% deterministic: median + MAD as the value-anomaly
baseline, a sliding-window transition detector (with an analog-skip
heuristic), and a power-rail collapse-cluster detector — all funneled into
a `PatternFeatures` with short, number-bearing `notable_findings`. The
**diagnostic agent** wraps Anthropic's SDK behind a `Protocol`-shaped seam
(`AnthropicClient`) so the integration test can substitute a deterministic
fake; the real client uses assistant-side JSON pre-fill to force strict JSON
output and validates every response against pydantic. **The mitigation
recommender** strictly matches `hypothesis.suggested_pattern_id` against the
packaged YAML library (5 starter patterns) and emits per-parameter
suggestion rationales + verification steps + standards citations. The
**report emitter** assembles per-DTC analyses into a UUIDv7-keyed bundle
(`report.json`, `report.html` via Jinja2, `manifest.json` with sha256s)
ready for hand-off to whatever review tool the user has.

## Key design decisions made autonomously

Every item below was an active choice; none was dictated by the spec. ADR
numbers reference files in `claude/decisions/`.

* **ADR-001 — Median + MAD baseline (instead of mean + stddev) for value
  anomaly detection.** Real ECU signals are non-Gaussian; outliers drag the
  mean and stddev toward themselves and self-erase. Median + MAD is robust
  to point contamination; 1.4826 scaling keeps the `deviation_sigmas`
  parameter intuitive.
* **ADR-002 — Strict pattern_id matching, not fuzzy LLM ranking.** Looking
  up `hypothesis.suggested_pattern_id` by equality (not Levenshtein, not
  embedding similarity, not a second LLM call). Reasoning: the LLM gets the
  full pattern list in its prompt, so the burden lies upstream where
  context is fullest. Unknown IDs are logged-and-dropped rather than
  raising — one bad hypothesis cannot kill the report.
* **ADR-003 — Assistant-side JSON pre-fill** for strict structured output.
  Anthropic doesn't expose a `response_format` toggle; pre-filling the
  assistant message with `{` forces the model into JSON immediately.
  Defensive strip of trailing markdown fences. On parse failure: log a
  500-char truncation and raise `DiagnosticParseError`.
* **ADR-004 — Auto-decoder fallback** when `--dbc` is omitted. Emits
  `frame_0x<id_hex>` synthetic signals from the first two LE bytes. Lets
  reviewers run DiagForge against an arbitrary ASC without authoring a
  DBC, at the cost of less-readable signal names in the report.
* **ADR-005 — Transition-anomaly detector skips analog signals.** First
  real run on the demo trace showed the bounce detector firing on the
  noisy `engine_rpm` idle wobble. Heuristic: skip signals with more than
  10 distinct values in the window. Discrete signals (2-state switches,
  3-state valves) still flow through; analog readings short-circuit.
* **ADR-006 — Makefile neutralizes PYTHONPATH and PYTHONNOUSERSITE.**
  Some hosts (mine, with ROS 2 installed) set `PYTHONPATH=/opt/ros/...`,
  which contaminates the poetry venv with `launch_testing` pytest plugins
  that fail to import their own deps. The Makefile defensively clears
  these so CI-style reproducibility doesn't depend on the developer's
  shell config.

Other smaller calls I made and the reasoning, none of which got their
own ADR but all of which were judgment calls:

* **UUIDv7 hand-rolled** (instead of pulling `uuid7` from a third-party
  package). RFC 9562 is short enough to implement in 12 lines and we
  already had `os.urandom`. No new dependency.
* **`pyproject.toml` uses `mypy --strict`** rather than the more relaxed
  default. The `[[tool.mypy.overrides]]` block silences `python-can` and
  `cantools` which lack type stubs; everything else is strict.
* **Ruff replaces flake8 + isort + pyupgrade + bugbear.** Single tool,
  faster, consistent config in `pyproject.toml`.
* **`pytest --cov=diagforge` always on** via `addopts`, so a developer
  running `pytest` directly still gets coverage output and accidentally-
  uncovered code shows up immediately.
* **Test fixtures live in `tests/fixtures/`**, not in `tests/unit/`, so
  the same fixtures are available to integration tests.
* **`ANTHROPIC_API_KEY` checked in the CLI itself**, not just in the SDK
  wrapper. Failing fast at command-parse time gives a clear error message
  rather than a stack trace deep in `httpx`.
* **`--model` is a CLI flag with `claude-sonnet-4-6` default.** Lets a
  reviewer try `claude-opus-4-7` (the fallback specified in CLAUDE.md)
  without code changes.
* **Bytes payload kept as `bytes`** through the model boundary (not
  `list[int]` or hex string). Pydantic v2 round-trips `bytes` to JSON as
  base64 cleanly — preserves wire-format fidelity for re-ingestion.
* **`Report` model uses `extra="forbid"`** at every layer. A field the
  schema doesn't know about → ValidationError, not a silent drop.
* **`DtcAnalysis.dtc` uses `DtcInfo`** (the report-facing model), not
  `DTCSnapshot` (the ingestion-facing model). Two models intentionally:
  ingestion carries optional fields the report flattens, and the report
  is a stable contract while ingestion can evolve. The CLI bridges them.
* **`prompt_hash` recorded on every DiagnosticResult.** SHA-256 of the
  exact system+user text sent to the model. Reproducibility-debugging:
  given a report you can reconstruct the inputs.
* **HTML uses inline CSS, no JS, no fonts.** Renders in any browser or
  email-archive viewer. Bundle-friendly.
* **Per-dropout duration extracted by regex-on-description, not by
  threading the number through the pydantic model.** Quick, ugly,
  works for now. A Phase 0 cleanup would add a structured
  `anomaly.evidence` field with native numeric duration; we deferred
  because no other consumer needs the structured form yet.
* **Property-based tests (hypothesis) used only on the statistics
  functions**, not on the whole analyzer. Property tests on the full
  builder would mostly assert "doesn't crash"; the targeted statistics
  tests are more informative.
* **The mitigation library loader supports multi-file merging**, even
  though Phase 0-Lite ships exactly one YAML. Cheap to add now; lets
  third parties add their own pattern files in Phase 1 without touching
  loader code.
* **Integration test mocks via `monkeypatch.setattr` on the CLI module's
  `RealAnthropicClient` symbol**, not via a global registry or DI
  container. Lightest-weight way to inject a fake.
* **Seed for the synthetic trace generator is `0xD1A9F07E`.** I tried 3-4
  seeds and picked the one whose dropouts looked realistic — irregular
  spacing, varied depths, jittered timestamps. Reproducibility is
  preserved because the seed is committed.
* **Pre-dropout idle is 50 frames; post-dropout idle is 20 frames.**
  Long enough that the median+MAD baseline is dominated by clean idle,
  short enough to keep the trace small and the demo fast.
* **Each dropout is 3 consecutive frames wide (~30ms) with 3-5 idle
  frames between dropouts.** A single-frame dropout would also be
  detectable, but multi-frame dropouts produce the more realistic
  ROADMAP-example finding (`durations: 30ms, 30ms, 30ms, 29ms`).
* **`make demo` requires `ANTHROPIC_API_KEY` and fails fast if unset.**
  The Makefile checks the env var before invoking poetry. Better than
  burning a poetry-venv startup.

## How to run the demo

```bash
export ANTHROPIC_API_KEY=sk-...
make demo
```

Or, more explicitly:

```bash
poetry run diagforge analyze \
    examples/p0300_intermittent_misfire/trace.asc \
    --dtcs examples/p0300_intermittent_misfire/dtcs.json \
    --dbc  examples/p0300_intermittent_misfire/engine.dbc \
    --output ./demo-output/ \
    --verbose
```

## Where the report is

```
demo-output/
├── report.json     (canonical structured report; validates against claude/report-schema.json)
├── report.html     (Jinja2-rendered human-readable summary; open in any browser)
└── manifest.json   (sha256 hashes + file sizes for the bundle)
```

## Coverage report

Final from `make test` after T0L.8:

```
82 passed, total coverage 90% (branch + line)
diagforge/_logging.py                       100%
diagforge/analyzer/timing.py                 79%   ← uncovered: helper edge branches
diagforge/cli.py                            100%
diagforge/diagnostic/agent.py                80%   ← uncovered: RealAnthropicClient (live SDK)
diagforge/diagnostic/prompts.py             100%
diagforge/ingestion/base.py                 100%
diagforge/ingestion/can_asc.py               83%
diagforge/ingestion/dtc_json.py              96%
diagforge/ingestion/models.py               100%
diagforge/ingestion/signal_decode.py        ~88%
diagforge/mitigation/library.py              93%
diagforge/mitigation/recommender.py         100%
diagforge/report/emitter.py                  98%
diagforge/report/html.py                    100%
diagforge/report/models.py                   97%
```

The two sub-80% files are intentional:

* **timing.py at 79%** — the uncovered branches are graceful-degeneracy
  guards (empty intervals, all-identical-values, malformed description
  text). Each is one or two lines; raising the coverage further requires
  contrived inputs that wouldn't surface in real diagnostic traces.
* **agent.py at 80%** — the uncovered code is the `RealAnthropicClient`
  wrapper around `anthropic.messages.create`. Covered only by `make demo`
  with a real API key; unit-testable only by mocking the SDK itself,
  which would just re-test the SDK's internals.

## Known limitations / TODOs

* **Live `make demo` was not run** during the autonomous session because
  no `ANTHROPIC_API_KEY` was present in the environment. The mocked
  integration test (`test_cli_analyze_produces_report`) exercises every
  line of the CLI pipeline end-to-end, so the wiring is proven correct;
  what is unproven is the actual prompt-response interaction with Claude.
  See `claude/BLOCKED.md` for the one-line user action needed.
* **No BLF, UDS, or J1939 ingestion** — Phase 0 tickets T0.1 through T0.3.
* **No multi-DTC correlation** — Phase 1 ticket T1.1. The current pipeline
  iterates DTCs independently.
* **No DBC-derived "discrete vs analog" hint** — ADR-005 uses a value-
  count heuristic instead. Some DBCs carry an explicit flag we could
  consume.
* **`MitigationMatch.parameter_suggestions.suggested_value` is always
  null** — only the rationale text is populated. Phase 0 ticket adds
  per-parameter numeric suggestion derived from `features`.
* **No correlation detection between signals** — `correlations` is an
  empty list in every report. Lays the schema down for Phase 0/1 but
  the analyzer doesn't populate it yet.
* **HTML template has no timing diagram** — Phase 1 ticket T0.9 wires
  matplotlib SVG into the template.
* **Per-dropout duration is parsed from anomaly description text via a
  small regex helper.** Works for the current English templates; would
  break if the description format changed without updating the parser.
  A Phase 0 cleanup adds structured `evidence.duration_us` to the
  anomaly model and drops the regex.
* **Auto-decoder collapses multi-signal frames** to one signal per frame
  ID. Acceptable for the demo (one signal in 0x100) but lossy on real
  buses. DBC mode is the supported workflow.

## Suggested final commit + tag commands (your call, not mine)

```bash
git tag v0.1.0          # tag the v0.1.0 release; do NOT push the tag until you've
                        # verified make demo against the live API
git remote add origin https://github.com/<you>/DiagForge.git
git push -u origin main
git push origin v0.1.0
```
