# Cluster 4 — CLI surfaces implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve gaps 4.1, 4.2, and 4.3 from the spec — add `kuroi diff` (text/html/json formats; pdf deferred to v1.1+), `kuroi models`, and global `-v` / `-vv` / `-q` verbosity flags with the document-text warning on first `-vv` per process.

**Architecture:** A new `core/diff.py` computes a structural diff between original and redacted PDFs (by comparing per-word bboxes); three renderers in `cli/diff.py` emit the same `Diff` value as text, JSON, and HTML. A new `cli/models.py` reads the packaged pricing table and queries the Ollama daemon (when reachable) to render the models view. Verbosity is plumbed through the stdlib `logging` module, configured once in `cli/__init__.py` based on the global `-v` / `-q` flags.

**Tech Stack:** Python 3.12+, Typer, Rich, PyMuPDF, stdlib `logging`, stdlib `html.escape`, pytest, mypy, ruff.

**Spec:** `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`, sections 4.1, 4.2, 4.3.

---

## File structure

**Created:**
- `src/kuroi/core/diff.py` — `Diff`, `DiffPage`, `DiffRedaction`, `compute_diff`.
- `src/kuroi/cli/diff.py` — `diff_app` Typer command with `--format` flag and renderers.
- `src/kuroi/cli/models.py` — `models_app` Typer command.
- `src/kuroi/core/log.py` — `setup_logging`, `warn_vv_once`.
- `tests/core/test_diff.py`
- `tests/cli/test_diff.py`
- `tests/cli/test_models.py`
- `tests/core/test_log.py`

**Modified:**
- `src/kuroi/cli/__init__.py` — register `diff_app` and `models_app`; install global `-v`/`-q` callback.

---

## Task 1: `Diff` data types and `compute_diff`

**Why this task exists:** A renderer-agnostic data structure decouples diff computation from rendering. Three renderers (Tasks 2-4) consume the same `Diff` value.

**Files:**
- Create: `src/kuroi/core/diff.py`
- Create: `tests/core/test_diff.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_diff.py`:

```python
from pathlib import Path

import pymupdf
import pytest

from kuroi.core.diff import Diff, compute_diff


def _two_pdfs(tmp_path: Path, original_text: str, redacted_text: str) -> tuple[Path, Path]:
    orig_path = tmp_path / "orig.pdf"
    red_path = tmp_path / "red.pdf"
    for text, p in ((original_text, orig_path), (redacted_text, red_path)):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        doc.save(str(p))
        doc.close()
    return orig_path, red_path


def test_compute_diff_finds_removed_words(tmp_path: Path):
    orig, red = _two_pdfs(
        tmp_path,
        "Sarah Chen was here",
        " was here",  # "Sarah Chen" removed
    )
    diff = compute_diff(orig, red)

    assert isinstance(diff, Diff)
    assert len(diff.pages) == 1
    page = diff.pages[0]
    assert page.page_number == 1
    redacted_texts = [r.before_text for r in page.redactions]
    assert "Sarah" in redacted_texts
    assert "Chen" in redacted_texts


def test_compute_diff_with_no_changes_yields_no_redactions(tmp_path: Path):
    orig, red = _two_pdfs(tmp_path, "same content", "same content")
    diff = compute_diff(orig, red)
    assert sum(len(p.redactions) for p in diff.pages) == 0


def test_compute_diff_mismatched_page_counts_raises(tmp_path: Path):
    orig_path = tmp_path / "orig.pdf"
    red_path = tmp_path / "red.pdf"

    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(str(orig_path))
    doc.close()

    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(red_path))
    doc.close()

    with pytest.raises(ValueError, match="page count"):
        compute_diff(orig_path, red_path)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_diff.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `core/diff.py`**

Create `src/kuroi/core/diff.py`:

```python
"""Compute and represent a per-page diff between an original and a redacted PDF.

The diff is structural (word-level bbox comparison), not audit-log-driven.
This means kuroi diff works on any redacted PDF, not just kuroi's own output.

The output `Diff` is renderer-agnostic; cli/diff.py turns it into text, JSON, or HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class DiffRedaction:
    """A single word-region present in the original but absent in the redacted output."""

    bbox: tuple[float, float, float, float]
    before_text: str


@dataclass(frozen=True)
class DiffPage:
    page_number: int
    before_text: str
    after_text: str
    redactions: tuple[DiffRedaction, ...]


@dataclass(frozen=True)
class Diff:
    pages: tuple[DiffPage, ...]


def compute_diff(original: Path, redacted: Path) -> Diff:
    """Compare `original` and `redacted` page-by-page; return a `Diff`."""
    orig_doc = pymupdf.open(str(original))
    red_doc = pymupdf.open(str(redacted))
    try:
        if len(orig_doc) != len(red_doc):
            raise ValueError(
                f"page count differs: {len(orig_doc)} vs {len(red_doc)}"
            )
        pages: list[DiffPage] = []
        for idx in range(len(orig_doc)):
            orig_page = orig_doc[idx]
            red_page = red_doc[idx]
            orig_words = orig_page.get_text("words")
            red_words = red_page.get_text("words")
            red_word_set = {(round(w[0], 1), round(w[1], 1), w[4]) for w in red_words}
            redactions: list[DiffRedaction] = []
            for x0, y0, x1, y1, word, *_ in orig_words:
                key = (round(x0, 1), round(y0, 1), word)
                if key not in red_word_set:
                    redactions.append(
                        DiffRedaction(bbox=(x0, y0, x1, y1), before_text=word)
                    )
            pages.append(
                DiffPage(
                    page_number=idx + 1,
                    before_text=orig_page.get_text("text"),
                    after_text=red_page.get_text("text"),
                    redactions=tuple(redactions),
                )
            )
        return Diff(pages=tuple(pages))
    finally:
        orig_doc.close()
        red_doc.close()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/test_diff.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/diff.py tests/core/test_diff.py
git commit -m "feat(diff): add Diff types and compute_diff word-level comparison"
```

---

## Task 2: `kuroi diff` command + text renderer

**Why this task exists:** Wires the CLI surface and the default text format.

**Files:**
- Create: `src/kuroi/cli/diff.py`
- Create: `tests/cli/test_diff.py`
- Modify: `src/kuroi/cli/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_diff.py`:

```python
from pathlib import Path

import pymupdf
from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def _make_pair(tmp_path: Path, original_text: str, redacted_text: str) -> tuple[Path, Path]:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    for text, p in ((original_text, a), (redacted_text, b)):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        doc.save(str(p))
        doc.close()
    return a, b


def test_diff_text_format_summarizes_each_page(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "Sarah Chen was here", "         was here")
    result = runner.invoke(app, ["diff", str(orig), str(red)])
    assert result.exit_code == 0
    assert "Page 1" in result.stdout
    assert "redaction" in result.stdout.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_diff.py::test_diff_text_format_summarizes_each_page -v`
Expected: FAIL — `diff` subcommand doesn't exist.

- [ ] **Step 3: Implement the command + text renderer**

Create `src/kuroi/cli/diff.py`:

```python
"""kuroi diff — show what changed between original and redacted PDFs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from kuroi.core.diff import Diff, compute_diff

diff_app = typer.Typer()
console = Console()

Format = Literal["text", "html", "json"]


@diff_app.callback(invoke_without_command=True)
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
        console.print(rendered)
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
```

(JSON and HTML renderers come in Tasks 3 and 4. Until then, calling them raises `NameError` — those tasks add them and lift the unused-imports warnings.)

- [ ] **Step 4: Register the command**

In `src/kuroi/cli/__init__.py`:

```python
from kuroi.cli.diff import diff_app

# ... after other add_typer calls:
app.add_typer(diff_app, name="diff", help="Show what changed between original and redacted.")
```

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/cli/test_diff.py -v`
Expected: PASS for the text test.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/diff.py src/kuroi/cli/__init__.py tests/cli/test_diff.py
git commit -m "feat(cli): add kuroi diff command with text format"
```

---

## Task 3: JSON renderer

**Why this task exists:** Pipeline-friendly NDJSON output, one line per page per spec section 4.1.

**Files:**
- Modify: `src/kuroi/cli/diff.py`
- Modify: `tests/cli/test_diff.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_diff.py`:

```python
import json


def test_diff_json_format_emits_ndjson(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "Sarah Chen was here", "         was here")
    result = runner.invoke(app, ["diff", str(orig), str(red), "--format", "json"])
    assert result.exit_code == 0
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    parsed = [json.loads(l) for l in lines]
    assert parsed[0]["page"] == 1
    assert "before_text" in parsed[0]
    assert "after_text" in parsed[0]
    assert "redactions" in parsed[0]
    assert isinstance(parsed[0]["redactions"], list)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_diff.py::test_diff_json_format_emits_ndjson -v`
Expected: FAIL — `render_json` undefined.

- [ ] **Step 3: Implement `render_json`**

Append to `src/kuroi/cli/diff.py`:

```python
import json


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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/cli/test_diff.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/diff.py tests/cli/test_diff.py
git commit -m "feat(diff): add NDJSON renderer"
```

---

## Task 4: HTML renderer

**Why this task exists:** Self-contained single-file output for case attachments.

**Files:**
- Modify: `src/kuroi/cli/diff.py`
- Modify: `tests/cli/test_diff.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_diff.py`:

```python
def test_diff_html_format_writes_self_contained_file(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "Sarah Chen was here", "         was here")
    out = tmp_path / "diff.html"
    result = runner.invoke(app, [
        "diff", str(orig), str(red), "--format", "html", "-o", str(out),
    ])
    assert result.exit_code == 0
    body = out.read_text()
    assert "<html" in body
    assert "<style" in body  # inline CSS, no external stylesheet
    assert "Sarah" in body
    assert "Page 1" in body


def test_diff_html_format_refuses_tty(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "x", "y")
    result = runner.invoke(app, ["diff", str(orig), str(red), "--format", "html"])
    assert result.exit_code == 2
    assert "requires -o" in result.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_diff.py::test_diff_html_format_writes_self_contained_file -v`
Expected: FAIL — `render_html` undefined.

- [ ] **Step 3: Implement `render_html`**

Append to `src/kuroi/cli/diff.py`:

```python
from html import escape


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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/cli/test_diff.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/diff.py tests/cli/test_diff.py
git commit -m "feat(diff): add self-contained HTML renderer"
```

---

## Task 5: `kuroi models` command — table output

**Why this task exists:** Spec section 4.2 specifies the output format. The static parts (Anthropic, OpenAI when added) come from the packaged pricing table; the Ollama section queries the daemon.

**Files:**
- Create: `src/kuroi/cli/models.py`
- Create: `tests/cli/test_models.py`
- Modify: `src/kuroi/cli/__init__.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_models.py`:

```python
from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_models_default_lists_anthropic_table():
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "Anthropic" in result.stdout
    assert "claude-opus-4-7" in result.stdout
    assert "$" in result.stdout
    assert "cloud" in result.stdout


def test_models_filter_by_provider_anthropic():
    result = runner.invoke(app, ["models", "anthropic"])
    assert result.exit_code == 0
    assert "Anthropic" in result.stdout
    assert "Ollama" not in result.stdout


def test_models_filter_unknown_provider():
    result = runner.invoke(app, ["models", "no-such-provider"])
    assert result.exit_code == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_models.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cli/models.py`**

Create `src/kuroi/cli/models.py`:

```python
"""kuroi models — list available LLM providers and their models."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import typer
from rich.console import Console

from kuroi.core.config import (
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
)
from kuroi.core.pricing import load_pricing

models_app = typer.Typer()
console = Console()


PRIVACY_POSTURE = {"anthropic": "cloud", "openai": "cloud", "ollama": "local"}
SEED_SUPPORT = {
    "anthropic": "temperature=0 only (best-effort, recorded in audit)",
    "openai": "full (system_fingerprint-bounded)",
    "ollama": "full",
}


@models_app.callback(invoke_without_command=True)
def models(
    provider: str | None = typer.Argument(None, help="Filter to one provider."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List available LLM providers and their models."""
    pricing = load_pricing()
    if provider is not None and provider not in pricing.providers:
        console.print(f"[red]unknown provider:[/] {provider}")
        raise typer.Exit(code=2)

    selected = (
        {provider: pricing.providers[provider]} if provider else pricing.providers
    )

    config = _resolve_config_or_default()

    if json_out:
        console.print(_json.dumps(_to_json(pricing, selected, config), indent=2))
        return

    for prov_name, models in selected.items():
        posture = PRIVACY_POSTURE.get(prov_name, "?")
        console.print(f"\n[bold]{prov_name.title()}[/]                                                   {posture}")
        installed: set[str] = set()
        if prov_name == "ollama":
            installed = _ollama_installed_models(config.ollama_url)
        for model_name, rates in models.items():
            if model_name == "*":
                continue
            default_marker = "(default)" if (
                config.provider == prov_name and config.model == model_name
            ) else ""
            install_marker = "(installed)" if model_name in installed else ""
            cost = (
                "free"
                if rates.input_per_million == 0.0 and rates.output_per_million == 0.0
                else f"${rates.input_per_million:.2f} / ${rates.output_per_million:.2f} per Mtok"
            )
            console.print(
                f"  {model_name:<22} {default_marker or install_marker:<11} {cost}"
            )
        console.print(f"  seed support: {SEED_SUPPORT.get(prov_name, 'unknown')}")

    console.print(
        f"\nDefault: {config.provider} / {config.model}   (configurable)"
    )
    console.print(f"Pricing last updated: {pricing.updated_at}")


def _resolve_config_or_default() -> Any:
    try:
        return resolve_config(
            ConfigOverrides(),
            env={},
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except Exception:  # noqa: BLE001
        # Tolerate config errors here — `kuroi models` is informational.
        from types import SimpleNamespace
        return SimpleNamespace(
            provider="anthropic",
            model="claude-opus-4-7",
            ollama_url="http://localhost:11434",
        )


def _ollama_installed_models(url: str) -> set[str]:
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=2.0)
        r.raise_for_status()
        data = r.json()
        return {entry["name"] for entry in data.get("models", [])}
    except (httpx.HTTPError, KeyError, ValueError):
        return set()


def _to_json(pricing: Any, selected: dict, config: Any) -> dict:
    return {
        "pricing_updated_at": pricing.updated_at,
        "default_provider": config.provider,
        "default_model": config.model,
        "providers": {
            prov_name: {
                "privacy": PRIVACY_POSTURE.get(prov_name, "unknown"),
                "seed_support": SEED_SUPPORT.get(prov_name, "unknown"),
                "models": {
                    m_name: {
                        "input_per_million": r.input_per_million,
                        "output_per_million": r.output_per_million,
                    }
                    for m_name, r in models.items()
                    if m_name != "*"
                },
            }
            for prov_name, models in selected.items()
        },
    }
```

- [ ] **Step 4: Register the command**

In `src/kuroi/cli/__init__.py`:

```python
from kuroi.cli.models import models_app

# ... after other add_typer calls:
app.add_typer(models_app, name="models", help="List available LLM providers and models.")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/cli/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/models.py src/kuroi/cli/__init__.py tests/cli/test_models.py
git commit -m "feat(cli): add kuroi models with pricing and ollama queries"
```

---

## Task 6: `kuroi models --json`

**Why this task exists:** The JSON path was sketched in Task 5 but not exercised. Confirm it works and add the test.

**Files:**
- Modify: `tests/cli/test_models.py`

- [ ] **Step 1: Add the test**

```python
import json as _json


def test_models_json_output_is_parseable():
    result = runner.invoke(app, ["models", "--json"])
    assert result.exit_code == 0
    data = _json.loads(result.stdout)
    assert "providers" in data
    assert "anthropic" in data["providers"]
    assert "models" in data["providers"]["anthropic"]
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/cli/test_models.py -v`
Expected: PASS (the JSON path was implemented in Task 5).

- [ ] **Step 3: Commit (if any test changes only)**

```bash
git add tests/cli/test_models.py
git commit -m "test(models): cover --json output path"
```

---

## Task 7: `core/log.py` — `setup_logging` and verbosity table

**Why this task exists:** Spec section 4.3 fixes what each verbosity level prints. We use stdlib `logging` so future code can call `log.info(...)` and `log.debug(...)` and have it routed correctly.

**Files:**
- Create: `src/kuroi/core/log.py`
- Create: `tests/core/test_log.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_log.py`:

```python
import logging

from kuroi.core.log import setup_logging


def test_setup_logging_default_is_warning(caplog):
    setup_logging(verbosity=0, quiet=False)
    log = logging.getLogger("kuroi")
    log.info("hello")
    assert "hello" not in caplog.text


def test_setup_logging_v_emits_info(caplog):
    setup_logging(verbosity=1, quiet=False)
    log = logging.getLogger("kuroi")
    with caplog.at_level(logging.INFO, logger="kuroi"):
        log.info("info-only")
        log.debug("debug-only")
    assert "info-only" in caplog.text
    assert "debug-only" not in caplog.text


def test_setup_logging_vv_emits_debug(caplog):
    setup_logging(verbosity=2, quiet=False)
    log = logging.getLogger("kuroi")
    with caplog.at_level(logging.DEBUG, logger="kuroi"):
        log.debug("debug-here")
    assert "debug-here" in caplog.text


def test_setup_logging_quiet_silences_info(caplog):
    setup_logging(verbosity=1, quiet=True)
    log = logging.getLogger("kuroi")
    with caplog.at_level(logging.INFO, logger="kuroi"):
        log.info("should-be-silent")
    # quiet wins over -v
    assert "should-be-silent" not in caplog.text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_log.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `core/log.py`**

Create `src/kuroi/core/log.py`:

```python
"""Verbosity plumbing.

`setup_logging(verbosity, quiet)` configures the `kuroi` logger so that:

  default       -> WARNING (only warnings/errors)
  -v            -> INFO   (findings list, token counts, rule fire counts, sizes)
  -vv           -> DEBUG  (full prompts, full responses, HTTP timing, traces)
  -q (any -v)   -> ERROR  (suppresses everything except final result + errors)

Calls to `console.print(...)` for user-facing default output are unaffected.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(verbosity: int, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    elif verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    root = logging.getLogger("kuroi")
    root.setLevel(level)
    # Idempotent — clear and re-attach a single stderr handler.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.propagate = False
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/test_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/log.py tests/core/test_log.py
git commit -m "feat(log): add setup_logging with -v/-vv/-q levels"
```

---

## Task 8: Wire `-v` / `-vv` / `-q` into the top-level CLI + the `-vv` warning

**Why this task exists:** Adds the global flags and the document-text warning emitted once per process when `-vv` is in use.

**Files:**
- Modify: `src/kuroi/cli/__init__.py`
- Modify: `src/kuroi/core/log.py`
- Modify: `tests/core/test_log.py`

- [ ] **Step 1: Write the failing test for the once-per-process warning**

Add to `tests/core/test_log.py`:

```python
def test_warn_vv_once_only_emits_once(capsys):
    from kuroi.core.log import warn_vv_once, _reset_vv_warning_flag

    _reset_vv_warning_flag()
    warn_vv_once()
    warn_vv_once()
    err = capsys.readouterr().err
    # Warning text appears exactly once even after two calls.
    assert err.count("note: -vv prints document text") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_log.py::test_warn_vv_once_only_emits_once -v`
Expected: FAIL — function does not exist.

- [ ] **Step 3: Implement `warn_vv_once`**

Append to `src/kuroi/core/log.py`:

```python
_VV_WARNED = False


def warn_vv_once() -> None:
    """Emit the document-text warning the first time -vv is in use this process."""
    global _VV_WARNED
    if _VV_WARNED:
        return
    _VV_WARNED = True
    print(
        "note: -vv prints document text to stderr; redirect if recording",
        file=sys.stderr,
    )


def _reset_vv_warning_flag() -> None:
    """Test hook only — do not call from production code."""
    global _VV_WARNED
    _VV_WARNED = False
```

- [ ] **Step 4: Wire into `cli/__init__.py`**

In `src/kuroi/cli/__init__.py`, replace the `main` callback:

```python
from kuroi.core.log import setup_logging, warn_vv_once


@app.callback()
def main(
    verbose: int = typer.Option(
        0, "-v", "--verbose", count=True,
        help="Show more detail (use -vv for debug).",
    ),
    quiet: bool = typer.Option(
        False, "-q", "--quiet", help="Show less.",
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """kuroi — strip sensitive data from PDFs with LLM assistance."""
    setup_logging(verbosity=verbose, quiet=quiet)
    if verbose >= 2:
        warn_vv_once()
```

- [ ] **Step 5: Add an integration test**

Add to `tests/core/test_log.py`:

```python
from typer.testing import CliRunner

from kuroi.cli import app
from kuroi.core.log import _reset_vv_warning_flag


def test_cli_vv_emits_warning_to_stderr():
    _reset_vv_warning_flag()
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(app, ["-vv", "--version"])
    assert result.exit_code == 0
    assert "note: -vv prints document text" in result.stderr
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/kuroi/core/log.py src/kuroi/cli/__init__.py tests/core/test_log.py
git commit -m "feat(cli): wire -v/-vv/-q top-level flags + document-text warning"
```

---

## Self-review checklist

- [ ] `uv run pytest tests/ -q` — all green.
- [ ] `uv run mypy src/` — no errors.
- [ ] `uv run ruff check src/ tests/` — no warnings.
- [ ] `kuroi diff a.pdf b.pdf` — emits per-page text summary.
- [ ] `kuroi diff a.pdf b.pdf --format json | jq .` — valid NDJSON.
- [ ] `kuroi diff a.pdf b.pdf --format html -o out.html` — self-contained HTML, opens in browser.
- [ ] `kuroi diff a.pdf b.pdf --format html` (no -o) — exit 2 with "requires -o".
- [ ] `kuroi models` — shows Anthropic table with pricing and seed-support note.
- [ ] `kuroi models anthropic` — filters to Anthropic only.
- [ ] `kuroi models --json` — emits parseable JSON.
- [ ] `kuroi -vv --version` — prints the document-text note once on stderr.
- [ ] `kuroi -v run ...` — emits info-level log lines (findings list, token counts).
- [ ] `kuroi -q run ...` — only the final result line and errors appear.

## Deferred

- **`--format pdf`** is v1.1+ per spec section 4.1. The renderer dispatch is structured to accept a fourth case; landing it later is a single function plus a dispatch arm.
- **`kuroi diff --audit <path>`** to enrich the diff with `kind` and `replacement` from the audit log — useful but not in the spec; track for a follow-up.
