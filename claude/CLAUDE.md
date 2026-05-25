# DiagForge — Project Context for Claude Code

You are working on **DiagForge**, an open-source CLI tool that ingests vehicle
diagnostic traces (CAN, CAN-FD, UDS, OBD-II), extracts timing and correlation
patterns around DTC occurrences, and uses an LLM to propose ranked root-cause
hypotheses with mitigation recommendations drawn from a curated pattern library.

**This file is the project constitution. Read it before making any decisions.**

## One-line mission

Give an embedded software engineer the first 80% of a DTC root cause analysis
in 30 seconds — with the reasoning visible, the mitigation pattern named, and
the verification approach suggested.

## Critical commit / attribution rules

1. **Never include "Co-authored-by: Claude" or any AI attribution in commit messages.**
2. **Never include AI emoji markers, "Generated with Claude Code", or similar.**
3. Commit messages are plain, conventional-commit style, ticket-referenced:
   - `T0L.3: implement timing pattern analyzer with windowed statistics`
   - `fix: ASC parser handles extended-ID frames`
4. The README must read as a single-author project. No "Built with Claude Code" anywhere public.
5. If you draft a commit message, suggest it — do not commit yourself.

## What we are NOT building

- Not a replacement for OEM diagnostic tools (Bosch ESI[tronic], wiTECH, ODIS).
- Not a real-time monitor. We analyze captured traces post-hoc.
- Not a generic log analyzer — deeply automotive (UDS, OBD-II, CAN, J1939).
- Not doing live ECU communication. Read-only on log files.
- Not reimplementing CAN parsing. Use `python-can` and `cantools`.

## Tech stack (do not change without ADR)

- Python 3.11+
- `click` for CLI
- `pydantic` v2 for all data models at module boundaries
- `python-can` for CAN log parsing
- `cantools` for DBC file parsing
- `udsoncan` for UDS message decoding (Phase 0 proper, not Phase 0-Lite)
- `anthropic` SDK (model: `claude-opus-4-7`, fallback `claude-sonnet-4-6`)
- `pytest` + `pytest-cov` + `hypothesis` for tests
- `jinja2` for HTML report templates
- `streamlit` for the Phase 1 UI (NOT in Phase 0)
- `ruff` + `black` for lint/format; `mypy --strict` for typing
- `poetry` for dependency management

## Build & test commands

```bash
make install       # poetry install + dep check
make test          # pytest with coverage report
make lint          # ruff + mypy --strict
make demo          # run the P0300 misfire demo case end-to-end
make build         # build pip package
```

Lint and tests must pass before any task is "done." If they don't pass and
you can't fix them, stop and surface the failure.

## Target project structure
diagforge/
├── CLAUDE.md                  # root pointer to claude/CLAUDE.md
├── claude/                    # all planning docs
│   ├── CLAUDE.md              # this file
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   ├── examples.md
│   ├── report-schema.json
│   ├── mitigation-patterns-starter.yaml
│   └── decisions/
├── pyproject.toml
├── Makefile
├── .gitignore
├── LICENSE
├── diagforge/                 # source code (created in T0L.1)
├── tests/
└── examples/                  # demo cases

## Always-do rules

1. **Lint and tests stay green.** Every commit. No exceptions.
2. **Pydantic at module boundaries.** No untyped dicts crossing module lines.
3. **External tools are wrapped and mockable.** Every `python-can`, `cantools`,
   `anthropic` call goes through a wrapper class with a fake counterpart for tests.
4. **The report schema is the source of truth.** `claude/report-schema.json` is
   canonical. Update the schema BEFORE changing the Python model.
5. **ADR every non-obvious decision.** One-paragraph note in `claude/decisions/NNN-title.md`.
6. **Use `logging`, not `print`.** INFO default, DEBUG behind `--verbose`.
7. **API key from `ANTHROPIC_API_KEY` env var.** Never in code, never in commits.
8. **Cite standards precisely.** Examples:
   - `ISO 14229-1:2020 §11.3` for UDS
   - `ISO 15031-5:2015 §6` for OBD-II
   - `SAE J1939-73 §5.7` for J1939 diagnostics

## Never-do rules

1. Never include AI attribution in commits, PRs, or public docs.
2. Never invent ISO clause numbers, SAE specs, or DTC codes. If unsure, leave a
   `TODO(verify)` comment and surface to the user.
3. Never silently drop a Claude API response. Parse failure → log raw (truncated) and raise typed exception.
4. Never `subprocess.run` or external call without explicit error handling.
5. Never write to `outputs/` or repo root from tests — use pytest `tmp_path`.
6. Never reproduce proprietary diagnostic protocol details from non-public sources.

## How the diagnostic agent must work (Layer 3)

When proposing root-cause hypotheses for a DTC pattern:

1. Receive structured input: DTC info, surrounding signal timing stats, transition
   and value anomalies from the deterministic analyzer.
2. Use strict structured output (JSON mode). Required fields per hypothesis:
   `rank`, `description`, `confidence` (low/medium/high), `evidence` (list of
   strings citing notable_findings), `suggested_pattern_id` (matches a mitigation
   library entry or null), `reasoning`.
3. Validate the response with pydantic. On parse failure, log raw (truncated 500 chars) and raise.
4. Never fabricate timing values. Quote actual numbers from the analyzer in `evidence`.
5. Each hypothesis MUST cite at least one item from `notable_findings`. If not,
   retry once with feedback added to the prompt, then raise `EvidenceMissingError`.

## Standards in scope

| Standard | Use |
|---|---|
| ISO 14229-1 (UDS) | DTC service decoding, service IDs |
| ISO 15031-5 (OBD-II) | Emissions diagnostic codes |
| ISO 15765 (Diagnostic Communication over CAN) | Transport layer |
| SAE J1939-73 | Heavy-duty diagnostics |
| ISO 11898 (CAN) | Frame format basics |

## Phase awareness

Always check `claude/ROADMAP.md` for current phase. Don't build Phase 1 features
during Phase 0. If a prompt seems to skip phases, ask whether to defer.

## When in doubt — ask

- "Which trace format should we support first?"
- "Should this match a mitigation pattern strictly or fuzzily?"
- "Are we still in Phase 0-Lite — should I implement this now or open an issue?"

Better to ask than to invent.
