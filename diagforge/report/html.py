"""Jinja2 HTML rendering for the report."""

from __future__ import annotations

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


def render_report_html(report: Report) -> str:
    template = _env().get_template(_TEMPLATE_NAME)
    return template.render(report=report)
