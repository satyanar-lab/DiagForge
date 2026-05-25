"""SVG timing-diagram renderer for the HTML report.

For each DTC analysis we render a single matplotlib chart showing every
decoded signal in the analysis window plus thin vertical markers at each
analyzer-reported anomaly. The figure is saved straight to an SVG string
and embedded inline in the Jinja2 template — no external image files, no
JavaScript, no font dependencies beyond matplotlib's defaults.

Sizing: we keep the figure narrow (8 in × 3.5 in), use thin lines, place
the legend outside the plot, and force the Agg backend so this works on
headless CI machines.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from diagforge.ingestion.models import TraceEvent
from diagforge.report.models import DtcAnalysis

_LINE_COLORS = (
    "#1f77b4",
    "#2ca02c",
    "#9467bd",
    "#8c564b",
    "#17becf",
    "#bcbd22",
)
_ANOMALY_COLOR = "#d96528"


def render_signal_chart(events: Sequence[TraceEvent], analysis: DtcAnalysis) -> str:
    """Return an SVG string. Empty string if there is nothing to plot."""
    signals: dict[str, list[tuple[int, float]]] = {}
    for ev in events:
        if not ev.decoded_signals:
            continue
        for name, value in ev.decoded_signals.items():
            signals.setdefault(name, []).append((ev.timestamp_us, value))

    if not signals:
        return ""

    fig, ax = plt.subplots(figsize=(8, 3.2))
    for i, (name, pts) in enumerate(signals.items()):
        xs = [t / 1000.0 for t, _ in pts]
        ys = [v for _, v in pts]
        ax.plot(
            xs,
            ys,
            linewidth=0.9,
            marker="",
            color=_LINE_COLORS[i % len(_LINE_COLORS)],
            label=name,
        )

    # Vertical markers for the first evidence timestamp of every anomaly.
    seen_marks: set[int] = set()
    for a in analysis.pattern_features.transition_anomalies:
        if not a.evidence_us:
            continue
        ts = a.evidence_us[0]
        if ts in seen_marks:
            continue
        seen_marks.add(ts)
        ax.axvline(ts / 1000.0, color=_ANOMALY_COLOR, alpha=0.55, linewidth=0.6)

    # DTC window box for orientation.
    if analysis.dtc.first_seen_us is not None and analysis.dtc.last_seen_us is not None:
        ax.axvspan(
            analysis.dtc.first_seen_us / 1000.0,
            analysis.dtc.last_seen_us / 1000.0,
            color="#fce4d6",
            alpha=0.35,
            label="DTC window",
        )

    ax.set_xlabel("time (ms)")
    ax.set_title(
        f"{analysis.dtc.code} ({analysis.dtc.standard}) — "
        f"{len(seen_marks)} anomaly event(s) in window"
    )
    ax.legend(loc="upper right", fontsize=7, framealpha=0.85)
    ax.grid(alpha=0.25, linewidth=0.4)
    ax.tick_params(labelsize=8)

    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue()
    # Strip the XML declaration and DOCTYPE so the SVG drops cleanly into HTML.
    if svg.startswith("<?xml"):
        svg = svg.split("?>", 1)[1].lstrip()
    if svg.startswith("<!DOCTYPE"):
        svg = svg.split(">", 1)[1].lstrip()
    return svg
