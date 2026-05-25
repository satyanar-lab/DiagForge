"""Jinja2 HTML rendering for the report."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import resources

from jinja2 import Environment, FileSystemLoader, select_autoescape

from diagforge.report.models import Report

_TEMPLATE_NAME = "report.html"


def _env() -> Environment:
    template_dir = resources.files("diagforge.report").joinpath("templates")
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_report_html(report: Report, charts: Sequence[str] | None = None) -> str:
    """Render the report HTML, optionally with one inline SVG chart per analysis.

    `charts` is a list aligned with `report.analyses`. An empty string at any
    index means "no chart for this DTC"; the template omits the figure block.
    """
    template = _env().get_template(_TEMPLATE_NAME)
    if charts is None:
        charts = [""] * len(report.analyses)
    if len(charts) != len(report.analyses):
        raise ValueError(f"charts length {len(charts)} != analyses length {len(report.analyses)}")
    return template.render(report=report, charts=list(charts))
