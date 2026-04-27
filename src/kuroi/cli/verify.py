"""kuroi verify — deterministic leak checker on a PDF."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.verification import verify_pdf

verify_app = typer.Typer(invoke_without_command=True)
console = Console()


@verify_app.callback(invoke_without_command=True)
def verify(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
) -> None:
    """Check an already-redacted PDF for residual sensitive data."""
    report = verify_pdf(pdf)

    if report.passed:
        console.print(f"  [green]PASS[/]  {pdf}: no residual leaks")
        raise typer.Exit(code=0)

    console.print(f"  [red]FAIL[/]  {pdf}: {len(report.leaks)} leaks detected\n")
    for leak in report.leaks:
        location = f"Page {leak.page}" if leak.page else "Document"
        console.print(f"    {location:<10} {leak.kind:<22} {leak.detail}")
        if leak.recovered_text:
            console.print(f"               recovered: {leak.recovered_text!r}")
    raise typer.Exit(code=4)
