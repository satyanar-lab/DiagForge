# ADR 008 — Trace parser dispatch via an extension registry

**Status:** accepted (T0.1)
**Date:** 2026-05-24

The Phase 0-Lite CLI hard-coded `AscTraceParser` and refused anything
whose extension wasn't `.asc`. T0.1 added UDS `.log` ingestion, and
Phase 0 still has BLF and Mode-04/09 ingestion to come, so a one-off
`elif` for each format would scale poorly.

`diagforge/ingestion/registry.py` introduces a tiny dispatch layer: a list
of `TraceParser` subclasses, and a `parser_for(path)` helper that returns
the first parser whose `extensions` tuple matches. Adding a new format is
one import + one list-append. `supported_extensions()` is the single
source of truth for what the CLI and Streamlit UI accept, so the help
text and validation never drift apart.

Trade-off: extension-only dispatch means a `.asc` file with garbage
contents is routed to `AscTraceParser` (which then errors), rather than
trying every parser in turn. Acceptable — content-sniffing would be
slower and ambiguous for the formats in scope (ASCII CAN logs are
visually indistinguishable from `.log` in a quick glance).
