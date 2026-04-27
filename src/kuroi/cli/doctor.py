"""kuroi doctor — diagnostic checks. No LLM calls."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

import typer
from rich.console import Console

from kuroi import __version__

doctor_app = typer.Typer(invoke_without_command=True)
console = Console()

CheckStatus = Literal["ok", "warn", "problem"]


@dataclass(frozen=True)
class CheckResult:
    label: str
    status: CheckStatus
    detail: str


def _python_version() -> CheckResult:
    v = sys.version_info
    label = f"Python version {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 12):
        return CheckResult(label, "ok", "")
    return CheckResult(label, "problem", "kuroi requires Python 3.12 or newer")


def _anthropic_key() -> CheckResult:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return CheckResult("Anthropic API key", "ok", "set (env: ANTHROPIC_API_KEY)")
    return CheckResult(
        "Anthropic API key",
        "problem",
        "ANTHROPIC_API_KEY is not set; cloud redaction is unavailable",
    )


def _binary_check(name: str) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, "ok", path)
    return CheckResult(name, "warn", f"{name} not found in PATH (optional for v0.1)")


@doctor_app.callback(invoke_without_command=True)
def doctor() -> None:
    """Check that everything kuroi needs is in working order."""
    checks: list[CheckResult] = [
        CheckResult(f"kuroi version {__version__}", "ok", ""),
        _python_version(),
        _anthropic_key(),
        _binary_check("tesseract"),
        _binary_check("qpdf"),
    ]

    glyph = {"ok": "[green]ok[/]", "warn": "[yellow]warn[/]", "problem": "[red]PROBLEM[/]"}
    for c in checks:
        line = f"  {c.label:<32} {c.detail:<40} {glyph[c.status]}"
        console.print(line)

    has_problem = any(c.status == "problem" for c in checks)
    if has_problem:
        console.print("\nOne or more checks failed. See messages above.")
        raise typer.Exit(code=1)
    console.print("\nEverything looks good.")
