"""DiagForge command-line entry point."""

from __future__ import annotations

import click

from diagforge import __version__


@click.group()
@click.version_option(__version__, prog_name="diagforge")
def main() -> None:
    """DiagForge — vehicle DTC root-cause co-pilot."""


@main.command()
def analyze() -> None:
    """Analyze a diagnostic trace (wired up in T0L.7)."""
    click.echo("analyze: not yet implemented (T0L.7)")


if __name__ == "__main__":
    main()
