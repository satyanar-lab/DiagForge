# ADR 010 — Streamlit `pipeline.py` separated from `app.py`

**Status:** accepted (T0.8)
**Date:** 2026-05-25

The Streamlit UI module has two files:

* `diagforge/ui/app.py` — the Streamlit script. Imports `streamlit as
  st`, defines no public functions, runs top-level statements that
  Streamlit's runtime re-executes on every interaction. **Not unit-
  testable** because Streamlit refuses to be imported outside a
  `streamlit run` session in many environments.
* `diagforge/ui/pipeline.py` — all the actual work: `run_pipeline()`,
  a `PipelineResult` dataclass, a zip-bundle helper. **No Streamlit
  import.** Fully unit-testable; the integration test
  `tests/integration/test_ui_pipeline.py` exercises it end-to-end with
  a mocked Anthropic client.

This split lets us assert that the UI's analysis path is identical to
the CLI's (same parsers, same recommender, same emitter) without
pretending we can fake the Streamlit runtime. `app.py` is excluded from
coverage measurement (declared in `pyproject.toml`) because a 0% file
would drag the total down; its job is layout, not logic, and the
PipelineResult contract it consumes is what the tests cover.

Trade-off: a UI-layout bug (wrong styling, broken file-uploader binding)
won't be caught by pytest. We accept that — Streamlit layout bugs are
visible the moment you `make ui`, and the alternative (Selenium-driving
a Streamlit instance) is heavier than the bug class warrants.
