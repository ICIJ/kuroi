"""kuroi diff — show what changed between original and redacted PDFs."""

from __future__ import annotations

import json
from html import escape
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


def render_html(d: Diff) -> str:
    style = (
        "body { font-family: sans-serif; margin: 2em; }"
        "h2 { border-bottom: 1px solid #ddd; }"
        ".page { margin-bottom: 2em; }"
        ".cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1em; }"
        ".col pre { background: #f6f6f6; padding: 0.5em; white-space: pre-wrap; }"
        ".redactions li { font-family: monospace; }"
    )
    parts: list[str] = ['<!doctype html><html><head><meta charset="utf-8">']
    parts.append("<title>kuroi diff</title>")
    parts.append(f"<style>{style}</style></head><body>")
    parts.append("<h1>kuroi diff</h1>")
    for page in d.pages:
        parts.append(f'<div class="page"><h2>Page {page.page_number} '
                     f'({len(page.redactions)} redactions)</h2>')
        parts.append('<div class="cols">')
        parts.append(f'<div class="col"><h3>Original</h3><pre>{escape(page.before_text)}</pre></div>')
        parts.append(f'<div class="col"><h3>Redacted</h3><pre>{escape(page.after_text)}</pre></div>')
        parts.append("</div>")
        if page.redactions:
            parts.append('<ul class="redactions">')
            for r in page.redactions:
                x0, y0, x1, y1 = r.bbox
                parts.append(
                    f"<li>[{x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}] "
                    f"<code>{escape(r.before_text)}</code></li>"
                )
            parts.append("</ul>")
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)
