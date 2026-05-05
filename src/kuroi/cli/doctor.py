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
from kuroi.cli.setup import probe_ollama_models
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
)

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


def _binary_check(name: str, detail_missing: str | None = None) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, "ok", path)
    missing = detail_missing if detail_missing is not None else f"{name} not found in PATH"
    return CheckResult(name, "warn", missing)


def _config_checks() -> list[CheckResult]:
    """Resolve config and report it. If provider=ollama, probe reachability."""
    try:
        config = resolve_config(
            ConfigOverrides(),
            env=os.environ,
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except ConfigError as exc:
        return [CheckResult("Config", "problem", f"{exc}")]
    results: list[CheckResult] = [
        CheckResult("Provider", "ok", config.provider),
        CheckResult("Model", "ok", config.model),
    ]
    if config.provider == "ollama":
        results.append(CheckResult("Ollama URL", "ok", config.ollama_url))
        models = probe_ollama_models(config.ollama_url)
        if models is None:
            results.append(
                CheckResult("Ollama reachability", "problem", f"unreachable at {config.ollama_url}")
            )
        else:
            results.append(
                CheckResult("Ollama reachability", "ok", f"{len(models)} model(s) installed")
            )
    return results


@doctor_app.callback(invoke_without_command=True)
def doctor() -> None:
    """Check that everything kuroi needs is in working order."""
    checks: list[CheckResult] = [
        CheckResult(f"kuroi version {__version__}", "ok", ""),
        _python_version(),
        _anthropic_key(),
        _binary_check("tesseract", detail_missing="required for scanned PDFs"),
        _binary_check("qpdf"),
        *_config_checks(),
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
