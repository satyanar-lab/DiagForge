"""Streamlit single-page UI for DiagForge.

Run with `make ui` (or `streamlit run diagforge/ui/app.py`).

The UI is intentionally minimal: three file uploaders + a single Analyze
button, a streaming status panel during the run, then expandable per-DTC
cards with the deterministic findings, the ranked hypotheses, the matched
mitigation patterns, the inline timing chart, and three download buttons
(JSON, HTML, full bundle .zip).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from diagforge import __version__
from diagforge._logging import configure_logging
from diagforge.ingestion.registry import supported_extensions
from diagforge.ui.pipeline import PipelineResult, run_pipeline

configure_logging(verbose=False)

st.set_page_config(
    page_title="DiagForge",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------- styling ----------------

st.markdown(
    """
    <style>
      /* Tighten the default Streamlit chrome */
      .block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1100px; }
      header[data-testid="stHeader"] { background: transparent; }
      h1 { font-weight: 700; letter-spacing: -0.01em; }
      h2 { font-weight: 600; border-bottom: 1px solid #d0d0d0; padding-bottom: 0.25rem; }
      .dtc-code { font-family: ui-monospace, Menlo, monospace; color: #b8430a; font-weight: 600; }
      .finding { background: #fff3e0; padding: 4px 8px; border-radius: 4px;
                 font-family: ui-monospace, monospace; font-size: 13px; }
      .hyp-card { border-left: 3px solid #d0d0d0; padding: 6px 14px;
                  margin: 6px 0; background: #fafafa; border-radius: 0 4px 4px 0; }
      .hyp-card.rank-1 { border-left-color: #d96528; }
      .conf-badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
                    font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
                    margin-left: 8px; vertical-align: middle; }
      .conf-low { background: #f0e0c0; color: #5a4500; }
      .conf-medium { background: #f0b97a; color: white; }
      .conf-high { background: #d96528; color: white; }
      .mitigation-card { background: #eef5fb; padding: 10px 14px; border-radius: 5px;
                         margin: 6px 0; border: 1px solid #cfdbe5; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- header + sidebar ----------------

st.title("DiagForge")
st.caption(
    f"Vehicle DTC root-cause co-pilot · v{__version__} · "
    "drop a trace + DTC snapshot, get ranked hypotheses with cited evidence."
)

with st.sidebar:
    st.header("Settings")
    model = st.selectbox(
        "Claude model",
        options=["claude-opus-4-7", "claude-sonnet-4-6"],
        index=0,
        help="opus is the default; sonnet is the fallback when opus is rate-limited.",
    )
    window_ms = st.slider(
        "Analysis window (ms)",
        min_value=100,
        max_value=3000,
        value=500,
        step=100,
        help="Window of trace data examined around each DTC's first/last occurrence.",
    )
    st.divider()
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if api_key_set:
        st.success("✓ ANTHROPIC_API_KEY is set")
    else:
        st.error("ANTHROPIC_API_KEY not set — export it and reload.")
    st.caption(
        f"Accepted trace formats: {', '.join(supported_extensions())}. "
        "DBC is optional (auto-decoder used when omitted)."
    )

# ---------------- file inputs ----------------

st.subheader("Inputs")
col_trace, col_dtcs, col_dbc = st.columns(3)
with col_trace:
    trace_file = st.file_uploader(
        "Trace file",
        type=[ext.lstrip(".") for ext in supported_extensions()],
        help="ASC (Vector ASCII CAN log) or .log (canutils / candump format).",
    )
with col_dtcs:
    dtcs_file = st.file_uploader(
        "DTC snapshot",
        type=["json"],
        help="DTC snapshot JSON — one entry per DTC.",
    )
with col_dbc:
    dbc_file = st.file_uploader(
        "DBC (optional)",
        type=["dbc"],
        help="Signal definitions for the bus. Without it, an auto-decoder is used.",
    )

can_run = bool(trace_file and dtcs_file and api_key_set)
analyze_clicked = st.button(
    "Analyze",
    type="primary",
    disabled=not can_run,
    use_container_width=False,
)

if not can_run and (trace_file or dtcs_file):
    missing = []
    if not trace_file:
        missing.append("trace")
    if not dtcs_file:
        missing.append("DTC snapshot")
    if not api_key_set:
        missing.append("ANTHROPIC_API_KEY")
    st.info(f"Waiting on: {', '.join(missing)}.")

# ---------------- run pipeline ----------------


def _save_to_temp(uploaded, target_dir: Path) -> Path:  # type: ignore[no-untyped-def]
    target = Path(target_dir) / str(uploaded.name)
    target.write_bytes(uploaded.getbuffer())
    return target


if analyze_clicked and trace_file and dtcs_file:
    tmp_dir = Path(tempfile.mkdtemp(prefix="diagforge_ui_"))
    trace_path = _save_to_temp(trace_file, tmp_dir)
    dtcs_path = _save_to_temp(dtcs_file, tmp_dir)
    dbc_path = _save_to_temp(dbc_file, tmp_dir) if dbc_file else None
    out_dir = tmp_dir / "out"

    progress_log: list[str] = []
    with st.status("Running diagnostic pipeline...", expanded=True) as status:

        def _step(msg: str) -> None:
            progress_log.append(msg)
            status.write(msg)

        try:
            pipeline_result = run_pipeline(
                trace_path=trace_path,
                dtcs_path=dtcs_path,
                dbc_path=dbc_path,
                out_dir=out_dir,
                model=model,
                window_ms=window_ms,
                progress=_step,
            )
            status.update(
                label=(f"Analysis complete · " f"{len(pipeline_result.report.analyses)} DTC(s)"),
                state="complete",
            )
            st.session_state["result"] = pipeline_result
        except Exception as exc:
            status.update(label=f"Failed: {exc}", state="error")
            st.exception(exc)

# ---------------- results ----------------


def _render_dtc_card(analysis, chart_svg: str) -> None:  # type: ignore[no-untyped-def]
    dtc = analysis.dtc
    header = (
        f"<span class='dtc-code'>{dtc.code}</span> "
        f"<small style='color:#666;'>[{dtc.standard}]</small>"
    )
    if dtc.description:
        header += f" — {dtc.description}"
    st.markdown(header, unsafe_allow_html=True)
    st.caption(
        f"occurrences: {dtc.occurrence_count or 1} · "
        f"first seen: {dtc.first_seen_us or '-'} µs · "
        f"last seen: {dtc.last_seen_us or '-'} µs"
    )

    if chart_svg:
        components.html(
            f"<div style='overflow-x:auto;'>{chart_svg}</div>",
            height=280,
            scrolling=False,
        )

    st.markdown("**Deterministic findings**")
    for f in analysis.pattern_features.notable_findings:
        st.markdown(f"<div class='finding'>{f}</div>", unsafe_allow_html=True)

    st.markdown("**Hypotheses**")
    for h in analysis.diagnostic_result.hypotheses:
        rank_class = "rank-1" if h.rank == 1 else ""
        conf_class = f"conf-{h.confidence}"
        st.markdown(
            f"<div class='hyp-card {rank_class}'>"
            f"<strong>#{h.rank}. {h.description}</strong>"
            f"<span class='conf-badge {conf_class}'>{h.confidence}</span>"
            f"<p style='margin: 6px 0;'>{h.reasoning}</p>"
            f"<div><em>Evidence:</em> "
            + ", ".join(f"<code class='finding'>{ev}</code>" for ev in h.evidence)
            + "</div>"
            + (
                f"<div><em>Suggested mitigation:</em> <code>{h.suggested_pattern_id}</code></div>"
                if h.suggested_pattern_id
                else ""
            )
            + "</div>",
            unsafe_allow_html=True,
        )

    if analysis.mitigation_matches:
        st.markdown("**Mitigation patterns**")
        for m in analysis.mitigation_matches:
            st.markdown(
                f"<div class='mitigation-card'><strong>{m.pattern_name}</strong> "
                f"<code>({m.pattern_id})</code></div>",
                unsafe_allow_html=True,
            )
            if m.parameter_suggestions:
                rows = []
                for p in m.parameter_suggestions:
                    value = "—" if p.suggested_value is None else f"`{p.suggested_value}`"
                    rows.append(
                        {"parameter": p.name or "-", "value": value, "rationale": p.rationale or ""}
                    )
                st.dataframe(rows, hide_index=True, use_container_width=True)
            if m.verification_steps:
                with st.expander("Verification steps"):
                    for step in m.verification_steps:
                        st.markdown(f"- {step}")
            if m.standards_references:
                with st.expander("Standards"):
                    for ref in m.standards_references:
                        st.markdown(f"- {ref}")


if "result" in st.session_state:
    result: PipelineResult = st.session_state["result"]
    st.markdown("---")
    st.subheader("Results")

    if result.report.cross_dtc_findings:
        st.markdown("**Cross-DTC findings**")
        for cf in result.report.cross_dtc_findings:
            badge = "🔗" if cf.type == "co_occurring" else "→"
            st.markdown(
                f"{badge} **{cf.type.replace('_', ' ')}** · "
                f"`{' / '.join(cf.dtc_codes)}` — {cf.description}"
            )
        st.markdown("---")

    dl_a, dl_b, dl_c = st.columns(3)
    with dl_a:
        st.download_button(
            "Download report.json",
            data=result.report_json_bytes,
            file_name="report.json",
            mime="application/json",
            use_container_width=True,
        )
    with dl_b:
        st.download_button(
            "Download report.html",
            data=result.report_html_bytes,
            file_name="report.html",
            mime="text/html",
            use_container_width=True,
        )
    with dl_c:
        st.download_button(
            "Download bundle.zip",
            data=result.bundle_zip_bytes,
            file_name="diagforge-audit-bundle.zip",
            mime="application/zip",
            use_container_width=True,
        )

    for i, analysis in enumerate(result.report.analyses):
        chart = result.charts[i] if i < len(result.charts) else ""
        with st.expander(
            f"{analysis.dtc.code} — {analysis.dtc.description or 'no description'}",
            expanded=True,
        ):
            _render_dtc_card(analysis, chart)
