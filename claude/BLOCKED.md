# BLOCKED — live demo step (one final action required from the user)

The autonomous session for Phase 0-Lite finished all code work and 8 of 8
tickets. The one acceptance criterion that requires real-world human input
is the live `make demo` run against the real Anthropic API.

## What was completed

All of Phase 0-Lite. See `claude/SESSION_SUMMARY.md` for the full picture.
The CLI works end-to-end and the integration test exercises the full
pipeline with a mocked Anthropic client (74 → 82 tests passing, 90% coverage,
`make lint` and `make test` both green).

## What is blocking the live demo

The environment did not have `ANTHROPIC_API_KEY` exported, and there is no
key file in the project tree (correctly excluded by `.gitignore`). The CLI
correctly refuses to run without the key, and `make demo` likewise.

## What you need to do

```bash
# 1. Export your Anthropic key (or use whatever your shell config provides):
export ANTHROPIC_API_KEY=sk-...

# 2. Run the demo:
make demo

# 3. Open the report in a browser:
xdg-open ./demo-output/report.html   # Linux
# or:  open ./demo-output/report.html  # macOS
```

You should see one `P0300` analysis card with a deterministic finding citing
4 RPM dropouts, one to three ranked hypotheses (the top-ranked one should
suggest the `dematuration_timer` mitigation pattern), and a mitigation card
with verification steps + standards references.

## Resume command (if anything else needs picking up)

```bash
cd /home/pavankumar/workspace/DiagForge
git status              # should be clean
make lint && make test  # both should still pass
make demo               # the live run
```

If `make demo` fails for any reason that isn't an API-key issue, the most
likely culprit is the model response failing the verbatim-evidence check.
Re-run with verbose logging:

```bash
poetry run diagforge analyze \
  examples/p0300_intermittent_misfire/trace.asc \
  --dtcs examples/p0300_intermittent_misfire/dtcs.json \
  --dbc  examples/p0300_intermittent_misfire/engine.dbc \
  --output ./demo-output/ \
  --verbose
```

The retry loop runs at most once on a missing-evidence failure; if both
attempts miss, `EvidenceMissingError` is raised with the offending
hypotheses logged.
