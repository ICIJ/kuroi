"""kuroi diff — show what changed between original and redacted PDFs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from kuroi.core.diff import Diff, compute_diff

diff_app = typer.Typer()
console = Console()

Format = Literal["text", "html", "json"]


def diff(
    original: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    redacted: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    format: str = typer.Option("text", "--format", help="text, html, or json."),
    output: Path | None = typer.Option(None, "-o", "--output"),
) -> None:
    """Show what changed between an original and a redacted PDF."""
    if format not in ("text", "html", "json"):
        console.print(f"[red]unknown format:[/] {format}")
        raise typer.Exit(code=2)
    if format in ("html",) and output is None:
        console.print(
            f"[red]{format} format requires -o <path> "
            "(refusing to write binary or html to a TTY)[/]"
        )
        raise typer.Exit(code=2)

    d = compute_diff(original, redacted)

    if format == "text":
        rendered = render_text(d)
    elif format == "json":
        rendered = render_json(d)
    elif format == "html":
        rendered = render_html(d)
    else:  # pragma: no cover
        raise AssertionError("unreachable")

    if output is None:
        # Use typer.echo (no Rich wrapping/formatting) so JSON stays on one line per record.
        typer.echo(rendered)
    else:
        output.write_text(rendered, encoding="utf-8")


def render_text(d: Diff) -> str:
    lines: list[str] = []
    for page in d.pages:
        n = len(page.redactions)
        lines.append(f"Page {page.page_number}: {n} redaction{'s' if n != 1 else ''}")
        for red in page.redactions:
            x0, y0, x1, y1 = red.bbox
            lines.append(f"  - [{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}]  {red.before_text!r}")
    return "\n".join(lines)


def render_json(d: Diff) -> str:
    lines: list[str] = []
    for page in d.pages:
        payload = {
            "page": page.page_number,
            "before_text": page.before_text,
            "after_text": page.after_text,
            "redactions": [
                {"bbox": list(r.bbox), "kind": "", "replacement": "", "before_text": r.before_text}
                for r in page.redactions
            ],
        }
        lines.append(json.dumps(payload, separators=(",", ":")))
    return "\n".join(lines)
