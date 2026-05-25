"""Versioned prompt templates for the diagnostic agent.

`PROMPT_TEMPLATE_VERSION` is recorded on every DiagnosticResult so that a
regenerated report can be tied back to the exact prompt that produced it.
Bump the version whenever the wording, schema demands, or evidence rules
change in a way that could shift model output.
"""

from __future__ import annotations

import hashlib
import json
import textwrap

from diagforge.ingestion.models import DTCSnapshot
from diagforge.report.models import PatternFeatures

PROMPT_TEMPLATE_VERSION = "diag-v1"

SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are a senior embedded ECU diagnostics engineer reviewing one
    vehicle diagnostic trouble code (DTC) at a time. You have access to
    the output of a deterministic timing/value-anomaly analyzer. You may
    only reason from that analyzer output and the standard diagnostic
    knowledge of UDS (ISO 14229), OBD-II (ISO 15031), and J1939.

    You must respond by calling the `submit_diagnostic_result` tool. Do
    not respond in plain text.

    Rules for the tool input:
    1. Every hypothesis's `evidence` array MUST cite at least one string
       quoted verbatim from `notable_findings`. Do not paraphrase.
    2. Never fabricate numbers, signal names, or ISO clause numbers.
    3. If a mitigation pattern from the supplied list fits, name it in
       `suggested_pattern_id` exactly; otherwise use null.
    4. Rank hypotheses 1..N with 1 = most likely.
    """
).strip()


def build_diagnostic_prompt(
    dtc: DTCSnapshot,
    features: PatternFeatures,
    available_pattern_ids: list[str],
    feedback: str | None = None,
) -> str:
    """Render the user-side prompt that wraps the analyzer output."""
    dtc_blob = json.dumps(dtc.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
    features_blob = json.dumps(
        features.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False
    )
    patterns_blob = json.dumps(available_pattern_ids, indent=2, ensure_ascii=False)

    extra = ""
    if feedback:
        extra = (
            "\n\nFEEDBACK FROM LAST ATTEMPT — read this before answering:\n"
            f"{feedback.strip()}\n"
            "Re-do the response. Each hypothesis must cite a verbatim string from notable_findings."
        )

    return textwrap.dedent(
        f"""\
        DTC under investigation:
        {dtc_blob}

        Deterministic analyzer output (PatternFeatures):
        {features_blob}

        Available mitigation pattern IDs (use exactly one of these or null):
        {patterns_blob}

        Call the `submit_diagnostic_result` tool with your ranked hypotheses.
        Each hypothesis's `evidence` array must quote at least one string
        verbatim from notable_findings above.{extra}
        """
    ).strip()


def prompt_hash(system: str, user: str) -> str:
    """SHA-256 of the system+user prompt — recorded in the report for traceability."""
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\n---\n")
    h.update(user.encode("utf-8"))
    return h.hexdigest()
