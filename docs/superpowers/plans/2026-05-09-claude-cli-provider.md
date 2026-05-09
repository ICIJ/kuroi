# Claude CLI Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third LLM provider, `claude-cli`, that drives the local `claude` binary via Anthropic's `claude-agent-sdk` so calls bill against the user's Claude Code subscription instead of the per-token Anthropic API.

**Architecture:** New `ClaudeCliProvider` class at `src/kuroi/providers/claude_cli.py` matches the existing `Provider` Protocol unchanged. It wraps `claude_agent_sdk.query()` (async) inside a sync `detect_redactions()` via `anyio.run`. `claude-agent-sdk` is added as a required dep. The `Provider` Protocol, the chunking layer, and the existing `AnthropicProvider` / `OllamaProvider` classes are not touched.

**Tech Stack:** Python 3.12, `claude-agent-sdk>=0.1.80`, `anyio>=4`, pytest, typer.

**Spec:** `docs/superpowers/specs/2026-05-09-claude-cli-provider-design.md` (commit `e087f13`).

**Spec correction surfaced during planning:** the spec says no pricing entry is needed because `estimate_cost` handles missing rates. That is incorrect — `core/pricing.py:42` raises `KeyError` for unknown providers. **Task 2 of this plan adds a wildcard-zero entry to `pricing.json`** (matching how Ollama is handled).

---

## File map

**Create:**
- `src/kuroi/providers/claude_cli.py` — `ClaudeCliProvider` (~200 lines)
- `tests/providers/test_claude_cli.py` — unit tests with stubbed `query_fn`
- `tests/providers/test_claude_cli_integration.py` — opt-in smoke test, gated on `claude` binary

**Modify:**
- `pyproject.toml` — add `claude-agent-sdk>=0.1.80` to `dependencies`
- `src/kuroi/data/pricing.json` — add `claude-cli` wildcard with zero rates
- `src/kuroi/core/config.py` — widen `ProviderName` + `VALID_PROVIDERS`, add `claude_cli_path` and `claude_cli_timeout_s` fields, wire resolution, update `write_config_file`
- `src/kuroi/providers/factory.py` — third dispatch arm
- `src/kuroi/cli/run.py` — `--claude-cli-path` flag
- `src/kuroi/cli/setup.py` — third provider option + probe
- `src/kuroi/cli/models.py` — `PRIVACY_POSTURE` / `SEED_SUPPORT` entries; subscription rendering
- `tests/providers/test_factory.py` — third dispatch test
- `docs/user-guide/providers.md` — comparison-table row + new tab
- `docs/reference/config.md` — `claude_cli.cli_path` / `claude_cli.timeout_s`
- `CHANGELOG.md` — unreleased line

---

## Phase 1: wiring (no provider class yet)

### Task 1: Add `claude-agent-sdk` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, under `[project] dependencies`, insert `"claude-agent-sdk>=0.1.80"` between `"anthropic>=0.40"` and `"pyyaml>=6.0"`:

```toml
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "pymupdf>=1.24",
    "anthropic>=0.40",
    "claude-agent-sdk>=0.1.80",
    "pyyaml>=6.0",
    "httpx>=0.27",
]
```

- [ ] **Step 2: Re-resolve and verify install**

Run: `uv sync`
Expected: dependency resolution succeeds; new wheels for `claude-agent-sdk`, `anyio`, `mcp`, and `sniffio` appear in the lockfile.

- [ ] **Step 3: Smoke-import the SDK**

Run: `uv run python -c "from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock, CLINotFoundError, ProcessError, CLIJSONDecodeError, CLIConnectionError; print('ok')"`
Expected output: `ok`

If any name does not exist in v0.1.80, note the actual name in `docs/superpowers/specs/2026-05-09-claude-cli-provider-design.md` open-questions section and adjust subsequent tasks to use the actual name. The plan assumes the names above; if any differ, every later task that imports them must be adjusted in lockstep.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add claude-agent-sdk for the upcoming claude-cli provider"
```

---

### Task 2: Add zero-rate pricing entry for `claude-cli`

**Files:**
- Modify: `src/kuroi/data/pricing.json`
- Test: existing `tests/core/test_pricing.py` (verify nothing breaks)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_pricing.py` (create the file if absent — but it likely exists; verify with `ls tests/core/test_pricing.py`):

```python
def test_claude_cli_pricing_rates_are_zero() -> None:
    from kuroi.core.pricing import load_pricing

    pricing = load_pricing()
    rates = pricing.rates("claude-cli", "claude-opus-4-7")
    assert rates.input_per_million == 0.0
    assert rates.output_per_million == 0.0
    assert rates.cache_write_multiplier == 0.0
    assert rates.cache_read_multiplier == 0.0


def test_claude_cli_pricing_uses_wildcard_for_unknown_model() -> None:
    from kuroi.core.pricing import load_pricing

    pricing = load_pricing()
    rates = pricing.rates("claude-cli", "totally-made-up-model")
    assert rates.input_per_million == 0.0
    assert rates.output_per_million == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pricing.py::test_claude_cli_pricing_rates_are_zero tests/core/test_pricing.py::test_claude_cli_pricing_uses_wildcard_for_unknown_model -v`
Expected: FAIL with `KeyError: 'claude-cli'`.

- [ ] **Step 3: Add the pricing entry**

Edit `src/kuroi/data/pricing.json`. Add `claude-cli` between `anthropic` and `ollama`:

```json
{
  "schema_version": 1,
  "updated_at": "2026-05-09",
  "providers": {
    "anthropic": {
      "claude-opus-4-7":   {"input_per_million": 15.00, "output_per_million": 75.00, "cache_write_multiplier": 1.25, "cache_read_multiplier": 0.1},
      "claude-sonnet-4-6": {"input_per_million":  3.00, "output_per_million": 15.00, "cache_write_multiplier": 1.25, "cache_read_multiplier": 0.1},
      "claude-haiku-4-5":  {"input_per_million":  0.80, "output_per_million":  4.00, "cache_write_multiplier": 1.25, "cache_read_multiplier": 0.1}
    },
    "claude-cli": {
      "*": {"input_per_million": 0.0, "output_per_million": 0.0, "cache_write_multiplier": 0.0, "cache_read_multiplier": 0.0}
    },
    "ollama": {
      "*": {"input_per_million": 0.0, "output_per_million": 0.0, "cache_write_multiplier": 0.0, "cache_read_multiplier": 0.0}
    }
  }
}
```

The wildcard `"*"` ensures `Pricing.rates("claude-cli", <any model>)` returns zero — subscription billing is not metered per token, so any cost computation correctly reports `$0.00`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_pricing.py -v`
Expected: PASS for both new tests; existing pricing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/data/pricing.json tests/core/test_pricing.py
git commit -m "feat(pricing): add zero-rate wildcard for claude-cli provider"
```

---

### Task 3: Widen `ProviderName` and `VALID_PROVIDERS`

**Files:**
- Modify: `src/kuroi/core/config.py:22` and `src/kuroi/core/config.py:149`
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_config.py`:

```python
def test_claude_cli_is_a_valid_provider() -> None:
    from kuroi.core.config import VALID_PROVIDERS

    assert "claude-cli" in VALID_PROVIDERS


def test_resolve_config_accepts_claude_cli_provider() -> None:
    from pathlib import Path

    from kuroi.core.config import ConfigOverrides, resolve_config

    cfg = resolve_config(
        ConfigOverrides(provider="claude-cli", model="claude-opus-4-7"),
        env={},
        file_path=Path("/nonexistent"),
    )
    assert cfg.provider == "claude-cli"
    assert cfg.model == "claude-opus-4-7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config.py::test_claude_cli_is_a_valid_provider tests/core/test_config.py::test_resolve_config_accepts_claude_cli_provider -v`
Expected: both FAIL — first with `AssertionError`, second with `ConfigError: Unknown provider: 'claude-cli'`.

- [ ] **Step 3: Widen the literal and the tuple**

Edit `src/kuroi/core/config.py:22`:

```python
ProviderName = Literal["anthropic", "ollama", "claude-cli"]
```

Edit `src/kuroi/core/config.py:149`:

```python
VALID_PROVIDERS: tuple[ProviderName, ...] = ("anthropic", "ollama", "claude-cli")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_config.py -v`
Expected: PASS for both new tests; existing config tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/config.py tests/core/test_config.py
git commit -m "feat(config): allow `claude-cli` as a provider name"
```

---

### Task 4: Add `claude_cli_path` and `claude_cli_timeout_s` to `Config` and `ConfigOverrides`

**Files:**
- Modify: `src/kuroi/core/config.py:55-65` and `src/kuroi/core/config.py:69-81`
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_config.py`:

```python
def test_config_has_claude_cli_path_default_none() -> None:
    from kuroi.core.config import Config

    cfg = Config(provider="anthropic", model="claude-opus-4-7", ollama_url="http://x")
    assert cfg.claude_cli_path is None
    assert cfg.claude_cli_timeout_s == 300


def test_config_overrides_defaults_have_claude_cli_fields_none() -> None:
    from kuroi.core.config import ConfigOverrides

    o = ConfigOverrides()
    assert o.claude_cli_path is None
    assert o.claude_cli_timeout_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config.py::test_config_has_claude_cli_path_default_none tests/core/test_config.py::test_config_overrides_defaults_have_claude_cli_fields_none -v`
Expected: both FAIL with `AttributeError: 'Config' object has no attribute 'claude_cli_path'` and similar for ConfigOverrides.

- [ ] **Step 3: Add the fields**

Edit `src/kuroi/core/config.py:55-65` so `Config` reads:

```python
@dataclass(frozen=True)
class Config:
    """Resolved configuration. After successful `resolve_config`, every field is set."""

    provider: ProviderName
    model: str
    ollama_url: str
    audit_include_text: bool = False
    backup_retention_hours: int = 24
    retry: RetryPolicy = DEFAULT_RETRY_POLICY
    layout_aware: bool = False
    claude_cli_path: str | None = None
    claude_cli_timeout_s: int = 300
```

Edit `src/kuroi/core/config.py:69-81` so `ConfigOverrides` reads:

```python
@dataclass(frozen=True)
class ConfigOverrides:
    """CLI-flag values to overlay on top of env, file, and built-ins.

    Each field is `None` when the corresponding flag was not passed.
    """

    provider: str | None = None
    model: str | None = None
    ollama_url: str | None = None
    retry_max: int | None = None
    retry_backoff: float | None = None
    retry_backoff_multiplier: float | None = None
    layout_aware: bool | None = None
    claude_cli_path: str | None = None
    claude_cli_timeout_s: int | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_config.py -v`
Expected: PASS for both new tests; existing config tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/config.py tests/core/test_config.py
git commit -m "feat(config): add claude_cli_path and claude_cli_timeout_s fields"
```

---

### Task 5: Wire config resolution for the new claude_cli fields

**Files:**
- Modify: `src/kuroi/core/config.py` (in `resolve_config`, around lines 297-367)
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_config.py`:

```python
def test_claude_cli_path_resolved_from_overrides(tmp_path) -> None:
    from kuroi.core.config import ConfigOverrides, resolve_config

    cfg = resolve_config(
        ConfigOverrides(
            provider="claude-cli",
            model="claude-opus-4-7",
            claude_cli_path="/usr/local/bin/claude",
        ),
        env={},
        file_path=tmp_path / "absent.toml",
    )
    assert cfg.claude_cli_path == "/usr/local/bin/claude"


def test_claude_cli_timeout_resolved_from_overrides(tmp_path) -> None:
    from kuroi.core.config import ConfigOverrides, resolve_config

    cfg = resolve_config(
        ConfigOverrides(
            provider="claude-cli",
            model="claude-opus-4-7",
            claude_cli_timeout_s=600,
        ),
        env={},
        file_path=tmp_path / "absent.toml",
    )
    assert cfg.claude_cli_timeout_s == 600


def test_claude_cli_path_resolved_from_toml(tmp_path) -> None:
    from kuroi.core.config import ConfigOverrides, resolve_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'provider = "claude-cli"\n'
        'model = "claude-opus-4-7"\n'
        '\n'
        '[claude_cli]\n'
        'cli_path = "/opt/claude"\n'
        'timeout_s = 900\n'
    )
    cfg = resolve_config(ConfigOverrides(), env={}, file_path=cfg_path)
    assert cfg.claude_cli_path == "/opt/claude"
    assert cfg.claude_cli_timeout_s == 900


def test_claude_cli_timeout_must_be_positive(tmp_path) -> None:
    import pytest

    from kuroi.core.config import ConfigError, ConfigOverrides, resolve_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'provider = "claude-cli"\n'
        'model = "claude-opus-4-7"\n'
        '\n'
        '[claude_cli]\n'
        'timeout_s = -1\n'
    )
    with pytest.raises(ConfigError, match="claude_cli.timeout_s"):
        resolve_config(ConfigOverrides(), env={}, file_path=cfg_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_config.py -k claude_cli -v`
Expected: the four new tests FAIL — overrides aren't read into Config; TOML keys aren't read; invalid values aren't rejected.

- [ ] **Step 3: Wire resolution**

In `src/kuroi/core/config.py`, inside `resolve_config` and just before the final `return Config(...)` call (around line 359), add:

```python
    # claude_cli table
    claude_cli_table = file_data.get("claude_cli", {})
    if not isinstance(claude_cli_table, dict):
        raise ConfigError(
            f"Expected table for `claude_cli`, got {type(claude_cli_table).__name__}"
        )

    claude_cli_path: str | None = (
        overrides.claude_cli_path
        or _read_string(file_data, "claude_cli.cli_path")
    )

    claude_cli_timeout_s: int = 300
    if "timeout_s" in claude_cli_table:
        raw = claude_cli_table["timeout_s"]
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            raise ConfigError(
                f"Expected positive integer for `claude_cli.timeout_s`, got {raw!r}"
            )
        claude_cli_timeout_s = raw
    if overrides.claude_cli_timeout_s is not None:
        if overrides.claude_cli_timeout_s <= 0:
            raise ConfigError(
                f"Expected positive integer for `claude_cli.timeout_s`, "
                f"got {overrides.claude_cli_timeout_s!r}"
            )
        claude_cli_timeout_s = overrides.claude_cli_timeout_s
```

Then change the final `return Config(...)` so the new fields are passed:

```python
    return Config(
        provider=provider,
        model=model,
        ollama_url=ollama_url,
        audit_include_text=audit_include_text_raw,
        backup_retention_hours=backup_retention_raw,
        retry=_read_retry_policy(file_data, env, overrides),
        layout_aware=_read_layout_aware(file_data, overrides),
        claude_cli_path=claude_cli_path,
        claude_cli_timeout_s=claude_cli_timeout_s,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_config.py -k claude_cli -v`
Expected: PASS for the four claude_cli resolution tests.

- [ ] **Step 5: Run the whole config test suite**

Run: `uv run pytest tests/core/test_config.py -v`
Expected: all pre-existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/config.py tests/core/test_config.py
git commit -m "feat(config): resolve claude_cli.cli_path and claude_cli.timeout_s"
```

---

### Task 6: Update `write_config_file` to emit `[claude_cli]` when set

**Files:**
- Modify: `src/kuroi/core/config.py:120-146`
- Test: `tests/core/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_config.py`:

```python
def test_write_config_file_omits_claude_cli_when_default(tmp_path) -> None:
    from kuroi.core.config import Config, write_config_file

    cfg = Config(
        provider="claude-cli",
        model="claude-opus-4-7",
        ollama_url="http://localhost:11434",
    )
    out = tmp_path / "config.toml"
    write_config_file(out, cfg)
    body = out.read_text()
    assert "[claude_cli]" not in body
    assert 'provider = "claude-cli"' in body


def test_write_config_file_emits_claude_cli_when_set(tmp_path) -> None:
    from kuroi.core.config import Config, write_config_file

    cfg = Config(
        provider="claude-cli",
        model="claude-opus-4-7",
        ollama_url="http://localhost:11434",
        claude_cli_path="/opt/claude",
        claude_cli_timeout_s=600,
    )
    out = tmp_path / "config.toml"
    write_config_file(out, cfg)
    body = out.read_text()
    assert "[claude_cli]" in body
    assert 'cli_path = "/opt/claude"' in body
    assert "timeout_s = 600" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config.py::test_write_config_file_omits_claude_cli_when_default tests/core/test_config.py::test_write_config_file_emits_claude_cli_when_set -v`
Expected: second test FAILS (the body is the same as today and never includes `[claude_cli]`).

- [ ] **Step 3: Update `write_config_file`**

Replace the body of `write_config_file` in `src/kuroi/core/config.py:120-146` with:

```python
def write_config_file(path: Path, config: Config) -> None:
    """Write `config` to `path` atomically.

    Serializes the on-disk schema (top-level `provider` and `model` plus a
    nested `[ollama]` table, optionally a `[claude_cli]` table when any
    field is non-default). Writes to `<path>.tmp` then `os.replace()` so
    a crash mid-write cannot corrupt an existing file.

    Assumes `config.provider`, `config.model`, `config.ollama_url`, and
    `config.claude_cli_path` contain no double-quote characters; the writer
    does not perform TOML escaping. This holds because `provider` is a
    `Literal`, and the others are validated upstream during config
    resolution.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f'provider = "{config.provider}"\n'
        f'model = "{config.model}"\n'
        f"\n"
        f"[ollama]\n"
        f'url = "{config.ollama_url}"\n'
    )
    has_custom_cli_path = config.claude_cli_path is not None
    has_custom_timeout = config.claude_cli_timeout_s != 300
    if has_custom_cli_path or has_custom_timeout:
        body += "\n[claude_cli]\n"
        if has_custom_cli_path:
            body += f'cli_path = "{config.claude_cli_path}"\n'
        if has_custom_timeout:
            body += f"timeout_s = {config.claude_cli_timeout_s}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_config.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/config.py tests/core/test_config.py
git commit -m "feat(config): write [claude_cli] table only when non-default"
```

---

## Phase 2: provider class (TDD)

### Task 7: Skeleton `ClaudeCliProvider` (no behavior yet)

**Files:**
- Create: `src/kuroi/providers/claude_cli.py`
- Create: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/providers/test_claude_cli.py`:

```python
"""Tests for ClaudeCliProvider — async-bridged subprocess via claude-agent-sdk."""

from __future__ import annotations

from kuroi.core.pdf import Page, Word
from kuroi.providers.claude_cli import ClaudeCliProvider


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_provider_has_name_and_default_model() -> None:
    provider = ClaudeCliProvider()
    assert provider.name == "claude-cli"
    assert provider.model == "claude-opus-4-7"


def test_short_circuit_when_no_categories_and_no_instructions() -> None:
    provider = ClaudeCliProvider(query_fn=_must_not_be_called)
    pages = (_page(1, ["Hello"]),)
    findings, chunks = provider.detect_redactions(pages, llm_category_ids=())
    assert findings == []
    assert chunks == []


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("query_fn should not be called when there is no work")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_claude_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kuroi.providers.claude_cli'`.

- [ ] **Step 3: Write the skeleton**

Create `src/kuroi/providers/claude_cli.py`:

```python
"""Claude CLI provider — drives the local `claude` binary via claude-agent-sdk.

Calls bill against the user's Claude Code subscription (no API key). The
sync `Provider.detect_redactions` interface is bridged to the SDK's async
`query()` via `anyio.run` per call. The event-loop spin-up cost is
negligible compared to the LLM round-trip.

Auth precedence: if `ANTHROPIC_API_KEY` is set in the environment, the CLI
prefers API (per-token) billing over OAuth/subscription. We do NOT strip
the variable; we log a warning at provider init so the user can decide.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers._shared import (
    build_system_prompt,
    build_user_document_block,
    build_user_static_prefix,
    parse_findings_payload,
)

logger = logging.getLogger("kuroi.providers.claude_cli")


class ClaudeCliProvider:
    """Provider that calls `claude` via claude-agent-sdk (subscription billing)."""

    name = "claude-cli"

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-7",
        cli_path: str | None = None,
        timeout_s: int = 300,
        query_fn: Any | None = None,
    ) -> None:
        self.model = model
        self._cli_path = cli_path
        self._timeout_s = timeout_s
        self._query_fn = query_fn  # injection point for tests; None → SDK's `query`
        if os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning(
                "claude-cli provider: ANTHROPIC_API_KEY is set in your "
                "environment, so the CLI may use API (per-token) billing "
                "instead of your subscription. Unset the variable if you "
                "want subscription billing."
            )

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
        model: str | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        del attempt  # accepted for Provider Protocol parity
        if not llm_category_ids and not instructions:
            return [], []
        raise NotImplementedError("filled in by Task 8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_claude_cli.py -v`
Expected: PASS for both tests.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(providers): scaffold ClaudeCliProvider with name + short-circuit"
```

---

### Task 8: Happy-path `detect_redactions`

**Files:**
- Modify: `src/kuroi/providers/claude_cli.py`
- Modify: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
import json
from dataclasses import dataclass


@dataclass
class _StubUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class _StubTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubAssistantMessage:
    def __init__(self, text: str) -> None:
        self.content = [_StubTextBlock(text)]


class _StubResultMessage:
    def __init__(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.usage = _StubUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _make_query_fn(messages: list[Any], captured: dict[str, Any] | None = None):
    """Build a stub `query` async-generator. Captures call args into `captured`."""

    async def stub(*, prompt: str, options: Any = None):
        if captured is not None:
            captured["prompt"] = prompt
            captured["options"] = options
        for m in messages:
            yield m

    return stub


def test_detect_redactions_round_trips_through_stub() -> None:
    findings_json = (
        '{"findings": [{"page": 1, "start": 1, "end": 2, '
        '"kind": "person_name", "confidence": "high"}]}'
    )
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage(findings_json),
            _StubResultMessage(input_tokens=1234, output_tokens=42),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-opus-4-7", query_fn=qfn)
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)

    findings, chunks = provider.detect_redactions(pages, llm_category_ids=("person_name",))

    assert len(findings) == 1
    assert findings[0].kind == "person_name"
    assert findings[0].source == "llm"
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.tokens_in == 1234
    assert chunk.tokens_out == 42
    assert chunk.pages == (1,)
    assert chunk.temperature == 0.0
    assert chunk.seed_honored is False
    assert chunk.system_fingerprint is None
    assert chunk.cache_creation_input_tokens == 0
    assert chunk.cache_read_input_tokens == 0
    # SHAs are deterministic for given inputs.
    assert len(chunk.prompt_sha256) == 64
    assert len(chunk.response_sha256) == 64
    # The prompt sent to the stub is static_prefix + document_block.
    assert "<document>" in captured["prompt"]
    assert "Active LLM categories: person_name" in captured["prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_detect_redactions_round_trips_through_stub -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the happy path**

Replace the `detect_redactions` body in `src/kuroi/providers/claude_cli.py`. Below the `if not llm_category_ids and not instructions: return [], []` line, write:

```python
        effective_model = model or self.model
        static_prefix = build_user_static_prefix(llm_category_ids, instructions)
        document_block = build_user_document_block(pages, layout_aware=layout_aware)
        user_prompt = static_prefix + document_block
        prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
        system_prompt = build_system_prompt(layout_aware)

        import anyio  # local import keeps Provider Protocol pure

        started = time.monotonic()
        text, tokens_in, tokens_out = anyio.run(
            self._aexec, user_prompt, system_prompt, effective_model
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        response_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

        logger.info(
            "claude-cli response duration_ms=%d tokens_in=%d tokens_out=%d "
            "response_chars=%d",
            duration_ms,
            tokens_in,
            tokens_out,
            len(text),
        )

        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=False,
            system_fingerprint=None,
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "claude-cli returned non-JSON response (%s; will subdivide). "
                "First 500 chars: %r",
                exc,
                text[:500],
            )
            return [], [chunk]
        if not isinstance(payload, dict):
            logger.warning(
                "claude-cli payload is not an object (will subdivide; got %s)",
                type(payload).__name__,
            )
            return [], [chunk]

        source = "instruction" if not llm_category_ids else "llm"
        return parse_findings_payload(payload, pages, source=source), [chunk]
```

Add the `_aexec` async helper as a method on `ClaudeCliProvider` directly below `detect_redactions`:

```python
    async def _aexec(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
    ) -> tuple[str, int, int]:
        """Run one `query()` call and return (text, tokens_in, tokens_out)."""
        from claude_agent_sdk import (  # local import; SDK is heavy
            ClaudeAgentOptions,
            query as sdk_query,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            max_turns=1,
            allowed_tools=[],
            permission_mode="default",
            setting_sources=[],
            cli_path=self._cli_path,
        )
        qfn = self._query_fn if self._query_fn is not None else sdk_query

        result_text = ""
        tokens_in = 0
        tokens_out = 0
        async for message in qfn(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for block in content:
                    text_attr = getattr(block, "text", None)
                    if isinstance(text_attr, str):
                        result_text += text_attr
            usage = getattr(message, "usage", None)
            if usage is not None:
                tokens_in = int(getattr(usage, "input_tokens", 0)) or tokens_in
                tokens_out = int(getattr(usage, "output_tokens", 0)) or tokens_out
        return result_text, tokens_in, tokens_out
```

Add at the top of the file (with the other imports):

```python
import json
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_detect_redactions_round_trips_through_stub -v`
Expected: PASS.

- [ ] **Step 5: Run the full provider test file**

Run: `uv run pytest tests/providers/test_claude_cli.py -v`
Expected: every existing test in the file still passes.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(providers): claude-cli happy path via claude-agent-sdk query"
```

---

### Task 9: Per-call model override

**Files:**
- Modify: `tests/providers/test_claude_cli.py` only

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_per_call_model_override_replaces_provider_default() -> None:
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage('{"findings": []}'),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-opus-4-7", query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    provider.detect_redactions(
        pages, ("person_name",), model="claude-haiku-4-5-20251001"
    )

    options = captured["options"]
    assert options.model == "claude-haiku-4-5-20251001"


def test_provider_default_model_used_when_no_override() -> None:
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage('{"findings": []}'),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-sonnet-4-6", query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    provider.detect_redactions(pages, ("person_name",))

    options = captured["options"]
    assert options.model == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_per_call_model_override_replaces_provider_default tests/providers/test_claude_cli.py::test_provider_default_model_used_when_no_override -v`
Expected: PASS — Task 8's `_aexec` already passes `model=effective_model`. This test pins the contract so a future refactor cannot silently break it.

- [ ] **Step 3: Commit**

```bash
git add tests/providers/test_claude_cli.py
git commit -m "test(claude-cli): pin per-call model override contract"
```

---

### Task 10: Malformed-JSON soft-fail

**Files:**
- Modify: `tests/providers/test_claude_cli.py` only (Task 8 already implements the behavior)

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_malformed_json_returns_empty_findings_with_chunk() -> None:
    qfn = _make_query_fn(
        [
            _StubAssistantMessage("this is not json"),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ]
    )
    provider = ClaudeCliProvider(query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    findings, chunks = provider.detect_redactions(pages, ("person_name",))

    assert findings == []
    assert len(chunks) == 1
    assert chunks[0].tokens_in == 10
    assert chunks[0].tokens_out == 2


def test_non_object_json_payload_returns_empty_findings_with_chunk() -> None:
    qfn = _make_query_fn(
        [
            _StubAssistantMessage("[1, 2, 3]"),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ]
    )
    provider = ClaudeCliProvider(query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    findings, chunks = provider.detect_redactions(pages, ("person_name",))

    assert findings == []
    assert len(chunks) == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_malformed_json_returns_empty_findings_with_chunk tests/providers/test_claude_cli.py::test_non_object_json_payload_returns_empty_findings_with_chunk -v`
Expected: PASS — already implemented in Task 8. This test pins the contract.

- [ ] **Step 3: Commit**

```bash
git add tests/providers/test_claude_cli.py
git commit -m "test(claude-cli): pin malformed-JSON soft-fail behavior"
```

---

### Task 11: `CLINotFoundError` → `ConfigError`

**Files:**
- Modify: `src/kuroi/providers/claude_cli.py`
- Modify: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_cli_not_found_raises_config_error() -> None:
    import pytest

    from kuroi.core.config import ConfigError

    class _FakeCLINotFound(Exception):
        pass

    async def qfn_raises(*, prompt: str, options: Any = None):
        raise _FakeCLINotFound("CLI binary not on PATH")
        yield  # pragma: no cover — make this an async generator

    provider = ClaudeCliProvider(query_fn=qfn_raises)
    pages = (_page(1, ["Hello"]),)

    # Patch the SDK's exception class so the provider catches our fake one.
    import kuroi.providers.claude_cli as mod

    monkeyed = {"orig": mod._cli_not_found_class}
    try:
        mod._cli_not_found_class = lambda: _FakeCLINotFound  # type: ignore[assignment]
        with pytest.raises(ConfigError, match="Claude CLI not found"):
            provider.detect_redactions(pages, ("person_name",))
    finally:
        mod._cli_not_found_class = monkeyed["orig"]  # type: ignore[assignment]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_cli_not_found_raises_config_error -v`
Expected: FAIL — the helper `_cli_not_found_class` does not exist yet, and `_FakeCLINotFound` propagates as a generic exception.

- [ ] **Step 3: Implement error mapping in the provider**

In `src/kuroi/providers/claude_cli.py`, just below the imports, add this helper indirection so tests can swap the exception class without monkey-patching the SDK module:

```python
def _cli_not_found_class():
    from claude_agent_sdk import CLINotFoundError

    return CLINotFoundError


def _process_error_class():
    from claude_agent_sdk import ProcessError

    return ProcessError


def _cli_json_decode_error_class():
    from claude_agent_sdk import CLIJSONDecodeError

    return CLIJSONDecodeError


def _cli_connection_error_class():
    from claude_agent_sdk import CLIConnectionError

    return CLIConnectionError


_AUTH_FAILURE_PATTERNS = ("not authenticated", "no credentials", "please log in")
```

Wrap the `anyio.run(self._aexec, ...)` call in `detect_redactions` with a try/except that maps SDK errors. Replace the line `text, tokens_in, tokens_out = anyio.run(...)` with:

```python
        try:
            text, tokens_in, tokens_out = anyio.run(
                self._aexec, user_prompt, system_prompt, effective_model
            )
        except _cli_not_found_class() as exc:
            from kuroi.core.config import ConfigError  # local to avoid cycle

            raise ConfigError(
                "Claude CLI not found. Install with `pip install "
                "claude-agent-sdk` or `npm install -g "
                "@anthropic-ai/claude-code`, then run `claude /login` to "
                f"authenticate. (SDK said: {exc})"
            ) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_cli_not_found_raises_config_error -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(claude-cli): map CLINotFoundError to ConfigError with install guidance"
```

---

### Task 12: `ProcessError` with auth marker → `ConfigError`

**Files:**
- Modify: `src/kuroi/providers/claude_cli.py`
- Modify: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_process_error_with_auth_marker_raises_config_error() -> None:
    import pytest

    import kuroi.providers.claude_cli as mod
    from kuroi.core.config import ConfigError

    class _FakeProcessError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeProcessError("not authenticated: run claude /login")
        yield  # pragma: no cover

    orig = mod._process_error_class
    mod._process_error_class = lambda: _FakeProcessError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        with pytest.raises(ConfigError, match="claude /login"):
            provider.detect_redactions((_page(1, ["Hi"]),), ("person_name",))
    finally:
        mod._process_error_class = orig  # type: ignore[assignment]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_process_error_with_auth_marker_raises_config_error -v`
Expected: FAIL — exception propagates without being mapped.

- [ ] **Step 3: Add the mapping branch**

In `src/kuroi/providers/claude_cli.py`, extend the try/except in `detect_redactions` immediately after the `_cli_not_found_class()` arm:

```python
        except _process_error_class() as exc:
            from kuroi.core.config import ConfigError  # local to avoid cycle

            message = str(exc).lower()
            if any(p in message for p in _AUTH_FAILURE_PATTERNS):
                raise ConfigError(
                    "Claude CLI is not authenticated. Run `claude /login` "
                    "to log in with your subscription account. (SDK said: "
                    f"{exc})"
                ) from exc
            logger.warning(
                "claude-cli ProcessError (will subdivide): %s", exc
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
```

Add the `_empty_chunk` helper at module level (this lets every soft-fail branch construct an identical chunk record):

```python
def _empty_chunk(
    pages: tuple[Page, ...],
    prompt_sha: str,
    seed: int | None,
) -> list[ChunkRecord]:
    """Audit chunk for a soft-failed call (no usable response text).

    Token counts and SHAs default to zero / the empty-string SHA, but the
    prompt SHA is preserved so a later replay can still re-run the same
    prompt.
    """
    return [
        ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=False,
            system_fingerprint=None,
            prompt_sha256=prompt_sha,
            response_sha256=hashlib.sha256(b"").hexdigest(),
            tokens_in=0,
            tokens_out=0,
            duration_ms=0,
        )
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_process_error_with_auth_marker_raises_config_error -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(claude-cli): map auth-failure ProcessError to ConfigError"
```

---

### Task 13: `ProcessError` (other) → soft-fail

**Files:**
- Modify: `tests/providers/test_claude_cli.py` only

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_process_error_without_auth_marker_returns_empty_findings() -> None:
    import kuroi.providers.claude_cli as mod

    class _FakeProcessError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeProcessError("the model timed out")
        yield  # pragma: no cover

    orig = mod._process_error_class
    mod._process_error_class = lambda: _FakeProcessError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        findings, chunks = provider.detect_redactions(
            (_page(1, ["Hi"]),), ("person_name",)
        )
        assert findings == []
        assert len(chunks) == 1
    finally:
        mod._process_error_class = orig  # type: ignore[assignment]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_process_error_without_auth_marker_returns_empty_findings -v`
Expected: PASS — already implemented in Task 12.

- [ ] **Step 3: Commit**

```bash
git add tests/providers/test_claude_cli.py
git commit -m "test(claude-cli): pin generic ProcessError soft-fail contract"
```

---

### Task 14: `CLIJSONDecodeError` → soft-fail

**Files:**
- Modify: `src/kuroi/providers/claude_cli.py`
- Modify: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_cli_json_decode_error_returns_empty_findings_with_chunk() -> None:
    import kuroi.providers.claude_cli as mod

    class _FakeJSONDecodeError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeJSONDecodeError("malformed envelope")
        yield  # pragma: no cover

    orig = mod._cli_json_decode_error_class
    mod._cli_json_decode_error_class = lambda: _FakeJSONDecodeError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        findings, chunks = provider.detect_redactions(
            (_page(1, ["Hi"]),), ("person_name",)
        )
        assert findings == []
        assert len(chunks) == 1
    finally:
        mod._cli_json_decode_error_class = orig  # type: ignore[assignment]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_cli_json_decode_error_returns_empty_findings_with_chunk -v`
Expected: FAIL — error propagates unmapped.

- [ ] **Step 3: Add the mapping branch**

In `src/kuroi/providers/claude_cli.py`, extend the try/except in `detect_redactions` immediately after the `_process_error_class()` arm:

```python
        except _cli_json_decode_error_class() as exc:
            logger.warning(
                "claude-cli CLIJSONDecodeError (will subdivide): %s", exc
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_cli_json_decode_error_returns_empty_findings_with_chunk -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(claude-cli): soft-fail on CLIJSONDecodeError"
```

---

### Task 15: `CLIConnectionError` → soft-fail

**Files:**
- Modify: `src/kuroi/providers/claude_cli.py`
- Modify: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_cli_connection_error_returns_empty_findings_with_chunk() -> None:
    import kuroi.providers.claude_cli as mod

    class _FakeConnectionError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeConnectionError("transient")
        yield  # pragma: no cover

    orig = mod._cli_connection_error_class
    mod._cli_connection_error_class = lambda: _FakeConnectionError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        findings, chunks = provider.detect_redactions(
            (_page(1, ["Hi"]),), ("person_name",)
        )
        assert findings == []
        assert len(chunks) == 1
    finally:
        mod._cli_connection_error_class = orig  # type: ignore[assignment]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_cli_connection_error_returns_empty_findings_with_chunk -v`
Expected: FAIL — error propagates unmapped.

- [ ] **Step 3: Add the mapping branch**

In `src/kuroi/providers/claude_cli.py`, extend the try/except in `detect_redactions` immediately after the `_cli_json_decode_error_class()` arm:

```python
        except _cli_connection_error_class() as exc:
            logger.warning(
                "claude-cli CLIConnectionError (will subdivide): %s", exc
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_cli_connection_error_returns_empty_findings_with_chunk -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(claude-cli): soft-fail on CLIConnectionError"
```

---

### Task 16: Timeout via `anyio.fail_after`

**Files:**
- Modify: `src/kuroi/providers/claude_cli.py`
- Modify: `tests/providers/test_claude_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_timeout_returns_empty_findings_with_chunk() -> None:
    import anyio

    async def qfn(*, prompt: str, options: Any = None):
        await anyio.sleep_forever()
        yield  # pragma: no cover

    provider = ClaudeCliProvider(query_fn=qfn, timeout_s=1)
    pages = (_page(1, ["Hi"]),)

    findings, chunks = provider.detect_redactions(pages, ("person_name",))

    assert findings == []
    assert len(chunks) == 1
```

- [ ] **Step 2: Skip the "verify it fails" step for this task**

Without the implementation, the stub `await anyio.sleep_forever()` would hang the test runner indefinitely. We verify by running step 4 *after* step 3 — if the implementation is broken, step 4 hangs and you cancel with Ctrl-C; that's the failure signal.

- [ ] **Step 3: Wrap `_aexec` with `anyio.fail_after`**

In `src/kuroi/providers/claude_cli.py`, replace the `_aexec` body so the SDK iteration runs inside `anyio.fail_after`:

```python
    async def _aexec(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
    ) -> tuple[str, int, int]:
        """Run one `query()` call and return (text, tokens_in, tokens_out)."""
        from claude_agent_sdk import (  # local import; SDK is heavy
            ClaudeAgentOptions,
            query as sdk_query,
        )
        import anyio

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            max_turns=1,
            allowed_tools=[],
            permission_mode="default",
            setting_sources=[],
            cli_path=self._cli_path,
        )
        qfn = self._query_fn if self._query_fn is not None else sdk_query

        result_text = ""
        tokens_in = 0
        tokens_out = 0
        with anyio.fail_after(self._timeout_s):
            async for message in qfn(prompt=prompt, options=options):
                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        text_attr = getattr(block, "text", None)
                        if isinstance(text_attr, str):
                            result_text += text_attr
                usage = getattr(message, "usage", None)
                if usage is not None:
                    tokens_in = int(getattr(usage, "input_tokens", 0)) or tokens_in
                    tokens_out = int(getattr(usage, "output_tokens", 0)) or tokens_out
        return result_text, tokens_in, tokens_out
```

In `detect_redactions`, extend the try/except immediately after the `_cli_connection_error_class()` arm:

```python
        except TimeoutError as exc:
            logger.warning(
                "claude-cli timed out after %ds (will subdivide): %s",
                self._timeout_s,
                exc,
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_timeout_returns_empty_findings_with_chunk -v`
Expected: PASS within ~1.5 seconds.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/claude_cli.py tests/providers/test_claude_cli.py
git commit -m "feat(claude-cli): cap query duration with anyio.fail_after"
```

---

### Task 17: `ClaudeAgentOptions` assertions

**Files:**
- Modify: `tests/providers/test_claude_cli.py` only

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_query_options_silence_the_agent_loop() -> None:
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage('{"findings": []}'),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-opus-4-7", query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    provider.detect_redactions(pages, ("person_name",))

    options = captured["options"]
    assert options.max_turns == 1
    assert options.allowed_tools == []
    assert options.permission_mode == "default"
    assert options.setting_sources == []
    assert options.model == "claude-opus-4-7"
    assert "kuroi" in options.system_prompt
    assert options.cli_path is None  # default; no override


def test_cli_path_override_propagates_to_options() -> None:
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage('{"findings": []}'),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ],
        captured,
    )
    provider = ClaudeCliProvider(cli_path="/opt/claude", query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    provider.detect_redactions(pages, ("person_name",))

    options = captured["options"]
    assert options.cli_path == "/opt/claude"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_query_options_silence_the_agent_loop tests/providers/test_claude_cli.py::test_cli_path_override_propagates_to_options -v`
Expected: PASS — already enforced by `_aexec`. This test pins the contract.

- [ ] **Step 3: Commit**

```bash
git add tests/providers/test_claude_cli.py
git commit -m "test(claude-cli): pin agent-silencing options"
```

---

### Task 18: `ANTHROPIC_API_KEY` warning at init

**Files:**
- Modify: `tests/providers/test_claude_cli.py` only (Task 7 already implements the warning)

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_claude_cli.py`:

```python
def test_anthropic_api_key_in_env_emits_warning(caplog, monkeypatch) -> None:
    import logging

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with caplog.at_level(logging.WARNING, logger="kuroi.providers.claude_cli"):
        ClaudeCliProvider()
    messages = [r.message for r in caplog.records]
    assert any("ANTHROPIC_API_KEY" in m and "subscription" in m for m in messages)


def test_no_warning_when_api_key_unset(caplog, monkeypatch) -> None:
    import logging

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="kuroi.providers.claude_cli"):
        ClaudeCliProvider()
    messages = [r.message for r in caplog.records]
    assert not any("ANTHROPIC_API_KEY" in m for m in messages)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/providers/test_claude_cli.py::test_anthropic_api_key_in_env_emits_warning tests/providers/test_claude_cli.py::test_no_warning_when_api_key_unset -v`
Expected: PASS — already implemented in Task 7.

- [ ] **Step 3: Run the full provider test file**

Run: `uv run pytest tests/providers/test_claude_cli.py -v`
Expected: every test passes.

- [ ] **Step 4: Commit**

```bash
git add tests/providers/test_claude_cli.py
git commit -m "test(claude-cli): pin ANTHROPIC_API_KEY init-time warning"
```

---

## Phase 3: integration

### Task 19: Factory dispatch

**Files:**
- Modify: `src/kuroi/providers/factory.py`
- Modify: `tests/providers/test_factory.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_factory.py`:

```python
def test_make_provider_dispatches_to_claude_cli() -> None:
    from kuroi.providers.claude_cli import ClaudeCliProvider

    cfg = Config(
        provider="claude-cli",
        model="claude-opus-4-7",
        ollama_url="http://localhost:11434",
        claude_cli_path="/opt/claude",
        claude_cli_timeout_s=600,
    )
    provider = make_provider(cfg)
    assert isinstance(provider, ClaudeCliProvider)
    assert provider.model == "claude-opus-4-7"
    assert provider._cli_path == "/opt/claude"
    assert provider._timeout_s == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/providers/test_factory.py::test_make_provider_dispatches_to_claude_cli -v`
Expected: FAIL with `ValueError: Unknown provider: 'claude-cli'`.

- [ ] **Step 3: Add the dispatch arm**

Edit `src/kuroi/providers/factory.py`. Replace the entire body of `make_provider`:

```python
"""Construct a Provider from a resolved Config."""

from __future__ import annotations

from kuroi.core.config import Config
from kuroi.providers.anthropic import AnthropicProvider
from kuroi.providers.base import Provider
from kuroi.providers.claude_cli import ClaudeCliProvider
from kuroi.providers.ollama import OllamaProvider


def make_provider(config: Config) -> Provider:
    """Return the Provider instance described by `config`."""
    if config.provider == "anthropic":
        return AnthropicProvider(model=config.model)
    if config.provider == "ollama":
        return OllamaProvider(model=config.model, url=config.ollama_url)
    if config.provider == "claude-cli":
        return ClaudeCliProvider(
            model=config.model,
            cli_path=config.claude_cli_path,
            timeout_s=config.claude_cli_timeout_s,
        )
    raise ValueError(f"Unknown provider: {config.provider!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_factory.py -v`
Expected: every test passes (existing two plus the new one).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/factory.py tests/providers/test_factory.py
git commit -m "feat(providers): factory dispatch for claude-cli"
```

---

### Task 20: `--claude-cli-path` and `--claude-cli-timeout` CLI flags

**Files:**
- Modify: `src/kuroi/cli/run.py:97-152`
- Modify: `src/kuroi/cli/run.py:194-209` (the `resolve_config` call)
- Modify: `tests/cli/test_run.py` if it exists, otherwise add coverage in `tests/cli/test_run_options.py`

- [ ] **Step 1: Write the failing test**

Look at existing CLI tests with `ls tests/cli/` and pick the file that covers `kuroi run`'s flag wiring. Append to it (or create `tests/cli/test_claude_cli_flags.py`):

```python
"""Tests for `kuroi run` CLI flags that resolve into ClaudeCliProvider."""

from __future__ import annotations

from typer.testing import CliRunner

from kuroi.cli import app


def test_claude_cli_path_flag_is_recognized() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert "--claude-cli-path" in result.stdout
    assert "--claude-cli-timeout" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_claude_cli_flags.py -v`
Expected: FAIL — neither flag appears in `--help` yet.

- [ ] **Step 3: Add the flags**

In `src/kuroi/cli/run.py`, immediately after the `ollama_url` parameter (around line 111), insert:

```python
    claude_cli_path: str | None = typer.Option(
        None,
        "--claude-cli-path",
        help=(
            "Path to the local `claude` binary for the claude-cli provider. "
            "Defaults to the binary bundled with claude-agent-sdk."
        ),
    ),
    claude_cli_timeout: int | None = typer.Option(
        None,
        "--claude-cli-timeout",
        help=(
            "Per-call timeout in seconds for the claude-cli provider. "
            "Default 300."
        ),
        min=1,
    ),
```

Then in the `resolve_config(ConfigOverrides(...))` call (around lines 198-209), add the new fields to `ConfigOverrides(...)`:

```python
                config = resolve_config(
                    ConfigOverrides(
                        provider=provider_name,
                        model=model,
                        ollama_url=ollama_url,
                        retry_max=max_retries,
                        retry_backoff=retry_backoff,
                        retry_backoff_multiplier=retry_backoff_multiplier,
                        layout_aware=layout_aware,
                        claude_cli_path=claude_cli_path,
                        claude_cli_timeout_s=claude_cli_timeout,
                    ),
                    env=os.environ,
                    file_path=xdg_config_home() / "kuroi" / "config.toml",
                )
```

Update the `--provider` help string (around line 100) so the listed options reflect reality:

```python
        help="LLM provider: 'anthropic', 'ollama', or 'claude-cli'. Overrides env and config.",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_claude_cli_flags.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full CLI test suite**

Run: `uv run pytest tests/cli/ -v`
Expected: every existing test still passes.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/
git commit -m "feat(cli): --claude-cli-path and --claude-cli-timeout flags"
```

---

### Task 21: `kuroi setup` interactive flow with probe

**Files:**
- Modify: `src/kuroi/cli/setup.py`
- Modify: `tests/cli/test_setup.py` (or whichever test file covers `setup`)

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_setup.py` (find the file with `ls tests/cli/`):

```python
def test_setup_offers_claude_cli_option(monkeypatch, tmp_path) -> None:
    """When the user picks 3, the config gets `provider = "claude-cli"`."""
    import os

    from kuroi.cli import setup as setup_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    answers = iter(["3", "1"])  # provider=3 (claude-cli), model=1 (opus)

    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *a, **kw: next(answers))
    # Stub the probe to succeed so setup writes the config.
    monkeypatch.setattr(setup_mod, "probe_claude_cli", lambda cli_path=None: True)

    setup_mod.setup()

    cfg = (tmp_path / "kuroi" / "config.toml").read_text()
    assert 'provider = "claude-cli"' in cfg
    assert 'model = "claude-opus-4-7"' in cfg


def test_setup_aborts_when_claude_cli_probe_fails(monkeypatch, tmp_path, capsys) -> None:
    import pytest
    import typer

    from kuroi.cli import setup as setup_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    answers = iter(["3", "1"])
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(setup_mod, "probe_claude_cli", lambda cli_path=None: False)

    with pytest.raises(typer.Exit):
        setup_mod.setup()
    cfg_path = tmp_path / "kuroi" / "config.toml"
    assert not cfg_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_setup.py -k claude_cli -v`
Expected: FAIL — the picker rejects "3"; `probe_claude_cli` does not exist.

- [ ] **Step 3: Wire the option into setup**

Edit `src/kuroi/cli/setup.py`. Add the probe near the top (after `probe_ollama_models`):

```python
def probe_claude_cli(cli_path: str | None = None) -> bool:
    """Verify the Claude CLI is reachable and authenticated. True on success.

    Runs a single one-shot `query()` with a tiny prompt and asserts a
    response message arrives. Returns False on any SDK error.
    """
    import anyio

    async def _ping() -> bool:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                query as sdk_query,
            )
        except ImportError:
            return False
        options = ClaudeAgentOptions(
            system_prompt="Reply with exactly: pong",
            max_turns=1,
            allowed_tools=[],
            permission_mode="default",
            setting_sources=[],
            cli_path=cli_path,
        )
        try:
            async for _ in sdk_query(prompt="ping", options=options):
                return True
        except Exception:
            return False
        return False

    try:
        return bool(anyio.run(_ping))
    except Exception:
        return False
```

Replace `_prompt_provider` so it offers three options:

```python
def _prompt_provider(default: str) -> str:
    console.print("[bold]Which LLM provider?[/]")
    console.print("  1. anthropic")
    console.print("  2. ollama")
    console.print("  3. claude-cli (uses your Claude Code subscription)")
    if default == "anthropic":
        default_index = "1"
    elif default == "ollama":
        default_index = "2"
    else:
        default_index = "3"
    choice = typer.prompt("Enter choice [1/2/3]", default=default_index)
    if choice.strip() in ("1", "anthropic"):
        return "anthropic"
    if choice.strip() in ("2", "ollama"):
        return "ollama"
    if choice.strip() in ("3", "claude-cli"):
        return "claude-cli"
    console.print(f"[yellow]Unrecognized choice {choice!r}; keeping {default}.[/]")
    return default
```

In `setup()`, add a third arm immediately before the `if provider_str not in VALID_PROVIDERS:` check (around line 160):

```python
    elif provider_str == "claude-cli":
        if not probe_claude_cli():
            console.print(
                "[red]Claude CLI is not reachable.[/] "
                "Install with `pip install claude-agent-sdk` or "
                "`npm install -g @anthropic-ai/claude-code`, then run "
                "`claude /login` to authenticate. Re-run `kuroi setup` "
                "when ready."
            )
            raise typer.Exit(code=2)
        default_model = (
            cur_model
            if cur_model in CURATED_ANTHROPIC_MODELS
            else CURATED_ANTHROPIC_MODELS[0]
        )
        model = _prompt_anthropic_model(default_model)
        ollama_url = cur_ollama_url
```

This goes between the existing `if provider_str == "anthropic":` and the `else:` that handles ollama. Restructure the `if`/`else` chain to:

```python
    if provider_str == "anthropic":
        ...  # existing anthropic block
    elif provider_str == "claude-cli":
        ...  # new block above
    else:
        ...  # existing ollama block
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_setup.py -v`
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/setup.py tests/cli/test_setup.py
git commit -m "feat(setup): add claude-cli provider option with live probe"
```

---

### Task 22: `kuroi models` listing for claude-cli

**Files:**
- Modify: `src/kuroi/cli/models.py:23-28` and the rendering loop at `src/kuroi/cli/models.py:49-70`
- Modify: `tests/cli/test_models.py` (if exists)

- [ ] **Step 1: Write the failing test**

Append to `tests/cli/test_models.py` (find with `ls tests/cli/`):

```python
def test_models_lists_claude_cli_with_subscription_label() -> None:
    from typer.testing import CliRunner

    from kuroi.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    out = result.stdout.lower()
    assert "claude-cli" in out
    assert "subscription" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_models.py::test_models_lists_claude_cli_with_subscription_label -v`
Expected: FAIL — "subscription" doesn't appear in output for claude-cli.

- [ ] **Step 3: Update the listing**

In `src/kuroi/cli/models.py:23-28`, extend the constants:

```python
PRIVACY_POSTURE = {
    "anthropic": "cloud",
    "openai": "cloud",
    "ollama": "local",
    "claude-cli": "cloud",
}
SEED_SUPPORT = {
    "anthropic": "temperature=0 only (best-effort, recorded in audit)",
    "openai": "full (system_fingerprint-bounded)",
    "ollama": "full",
    "claude-cli": "not available",
}
```

In the rendering loop at `src/kuroi/cli/models.py:57-69`, replace the `cost = "free" if ... else f"${...}"` block with:

```python
            if prov_name == "claude-cli":
                cost = "subscription billing"
            elif rates.input_per_million == 0.0 and rates.output_per_million == 0.0:
                cost = "free"
            else:
                cost = (
                    f"${rates.input_per_million:.2f} / "
                    f"${rates.output_per_million:.2f} per Mtok"
                )
```

`pricing.json` only declares a `"*"` wildcard for `claude-cli`, so the existing `if model_name == "*": continue` early-out hides it. To surface the three model IDs without per-model pricing entries, add this block before the `for model_name, rates in prov_models.items():` loop:

```python
        # claude-cli has only a wildcard pricing entry; expose the same
        # model IDs we list under anthropic so the user picker matches.
        if prov_name == "claude-cli":
            wildcard = prov_models.get("*")
            if wildcard is not None:
                from kuroi.cli.setup import CURATED_ANTHROPIC_MODELS

                prov_models = {m: wildcard for m in CURATED_ANTHROPIC_MODELS}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/models.py tests/cli/test_models.py
git commit -m "feat(cli): show claude-cli with subscription-billing label in `kuroi models`"
```

---

## Phase 4: smoke + docs

### Task 23: Opt-in integration smoke test

**Files:**
- Create: `tests/providers/test_claude_cli_integration.py`

- [ ] **Step 1: Write the smoke test**

Create `tests/providers/test_claude_cli_integration.py`:

```python
"""Opt-in smoke test for ClaudeCliProvider — runs a real `claude` query.

Skipped automatically if the binary is not on PATH, and excluded from the
default `make test` via the `slow` marker.
"""

from __future__ import annotations

import shutil

import pytest

from kuroi.core.pdf import Page, Word
from kuroi.providers.claude_cli import ClaudeCliProvider


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not installed",
    ),
]


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_real_query_returns_at_least_one_chunk() -> None:
    provider = ClaudeCliProvider(model="claude-haiku-4-5-20251001", timeout_s=120)
    pages = (_page(1, ["My", "name", "is", "Sarah", "Chen", "."]),)
    findings, chunks = provider.detect_redactions(pages, ("person_name",))
    assert len(chunks) == 1
    # Findings may be 0 if the model is conservative, so we don't assert > 0.
```

- [ ] **Step 2: Register the `slow` marker**

If `pyproject.toml` does not yet include the `slow` marker, append to `[tool.pytest.ini_options]`:

```toml
markers = [
    "slow: opt-in tests that hit external systems (run with `pytest -m slow`)",
]
```

Verify with: `grep -A3 'tool.pytest' pyproject.toml`. If the marker already exists, leave it alone.

- [ ] **Step 3: Run test (should be skipped if claude is missing)**

Run: `uv run pytest tests/providers/test_claude_cli_integration.py -v`
Expected: SKIPPED (with "claude CLI not installed" reason) on machines without the binary; PASS on a logged-in machine. If you want to force-run it on a machine that has `claude`, use `uv run pytest -m slow tests/providers/test_claude_cli_integration.py -v`.

- [ ] **Step 4: Verify default `make test` still excludes it**

Run: `uv run pytest -m "not slow"`
Expected: integration test does NOT appear in the run.

- [ ] **Step 5: Commit**

```bash
git add tests/providers/test_claude_cli_integration.py pyproject.toml
git commit -m "test(claude-cli): add opt-in integration smoke test"
```

---

### Task 24: Documentation

**Files:**
- Modify: `docs/user-guide/providers.md`
- Modify: `docs/reference/config.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update `docs/user-guide/providers.md`**

Replace the comparison table near the top of the file with:

```markdown
| Provider       | Hosting | API key required        | Cost                | Best for                           |
| -------------- | ------- | ----------------------- | ------------------- | ---------------------------------- |
| **Anthropic**  | Cloud   | Yes (`ANTHROPIC_API_KEY`) | Per-token         | Highest-quality on small batches.  |
| **Claude CLI** | Cloud   | No (subscription)       | Subscription        | Heavy use under a Claude Code plan.|
| **Ollama**     | Local   | No                      | Free (your hardware)| Offline / sensitive data.          |
```

After the existing `=== "Anthropic"` and `=== "Ollama"` tabs, add a new tab:

```markdown
=== "Claude CLI"

    Pass per invocation:

    ```sh
    $ pip install claude-agent-sdk          # or `npm install -g @anthropic-ai/claude-code`
    $ claude /login                          # one-time, authenticates your subscription
    $ kuroi run document.pdf --provider claude-cli --model claude-opus-4-7
    ```

    Or persist by editing `~/.config/kuroi/config.toml`:

    ```toml
    provider = "claude-cli"
    model = "claude-opus-4-7"

    # Optional — both fields default sensibly.
    [claude_cli]
    cli_path = "/usr/local/bin/claude"
    timeout_s = 300
    ```

    `kuroi setup` will probe the CLI, verify it's authenticated, and write
    this file for you.

    !!! note "ANTHROPIC_API_KEY shadowing"
        If `ANTHROPIC_API_KEY` is set in your environment when you select the
        `claude-cli` provider, the Claude CLI will prefer API (per-token)
        billing over your subscription. kuroi prints a warning at startup so
        the behavior is visible. Unset the variable to force subscription
        billing.

    Per-rule `model:` overrides (in your rule packs or category YAML) work
    the same way as for the Anthropic provider — the chunking layer dispatches
    per-model groups concurrently per batch:

    ```yaml
    - id: contact_info
      llm: true
      model: claude-haiku-4-5-20251001
    ```
```

Update the `kuroi models` example output a few lines below to include the new section:

```
Claude CLI                                                  cloud
  claude-opus-4-7        (default)   subscription billing
  claude-sonnet-4-6                  subscription billing
  claude-haiku-4-5-20251001          subscription billing
  seed support: not available
```

- [ ] **Step 2: Update `docs/reference/config.md`**

Find the `[ollama]` table reference and add a new section beneath it (use the existing wording style):

```markdown
### `[claude_cli]` table

| Key         | Type    | Default | Description                                            |
| ----------- | ------- | ------- | ------------------------------------------------------ |
| `cli_path`  | string  | unset   | Path to the `claude` binary. Unset → bundled CLI from `claude-agent-sdk`. |
| `timeout_s` | integer | `300`   | Per-call timeout for `claude-cli` provider invocations. |

These keys only apply when `provider = "claude-cli"`. The corresponding
CLI flags are `--claude-cli-path` and `--claude-cli-timeout`.
```

- [ ] **Step 3: Update `CHANGELOG.md`**

Find the unreleased section (or add one if absent) and prepend:

```markdown
- Add `claude-cli` provider that routes through the local Claude CLI via
  `claude-agent-sdk` (subscription billing, no `ANTHROPIC_API_KEY` required).
  Per-rule `model:` overrides apply on the same terms as the Anthropic
  provider.
```

- [ ] **Step 4: Run a docs build to catch broken refs**

Run: `uv run mkdocs build --strict` (or `uv run zensical build` if zensical is the configured generator — check `Makefile` to confirm).
Expected: build passes with no warnings.

- [ ] **Step 5: Commit**

```bash
git add docs/user-guide/providers.md docs/reference/config.md CHANGELOG.md
git commit -m "docs: claude-cli provider — guide, config reference, changelog"
```

---

## Self-review

**Spec coverage** — every section of `docs/superpowers/specs/2026-05-09-claude-cli-provider-design.md` mapped to one or more tasks:
- Architecture (file layout, async bridge, lazy import, dep) → Tasks 1, 7, 8
- Provider class signature → Task 7
- Per-call model override → Tasks 8, 9
- Prompt construction (`_shared` helpers, flattened system) → Task 8
- Silencing the agent loop (options) → Tasks 8, 17
- Reading the response → Tasks 8, 9
- Authentication (`ANTHROPIC_API_KEY` warning, no stripping) → Tasks 7, 18
- Error handling (CLINotFound, ProcessError auth, ProcessError other, JSON-decode, connection, timeout, unexpected re-raise) → Tasks 11–16
- Audit record mapping → Tasks 8, 12 (`_empty_chunk`)
- Pricing & `kuroi models` listing → Tasks 2, 22
- Configuration (TOML, CLI flag, factory) → Tasks 3–6, 19, 20
- `kuroi setup` flow with probe → Task 21
- Testing (unit + integration) → Tasks 7–18, 23
- Documentation → Task 24

**Spec-correction note inside the plan**: `pricing.json` MUST be updated (Task 2). The spec was wrong about `estimate_cost` tolerating missing rates.

**Open questions deferred to implementation** (per spec) — addressed:
- `ResultMessage.usage` field names → Task 1's smoke import + Task 8's `getattr(..., "input_tokens", 0)` defensive read.
- `disallowed_tools=["*"]` wildcard → not used; `allowed_tools=[]` plus `permission_mode="default"` is sufficient (the wildcard claim is dropped from the implementation).
- `setting_sources=[]` parameter name → assumed; if Task 1's smoke import fails or Task 7 fails to construct `ClaudeAgentOptions`, replace with the closest equivalent option name and update Task 17's assertion accordingly.

**Type consistency** — names verified across tasks:
- `ClaudeCliProvider`, `query_fn`, `_aexec`, `_empty_chunk`, `_cli_not_found_class`, `_process_error_class`, `_cli_json_decode_error_class`, `_cli_connection_error_class`, `_AUTH_FAILURE_PATTERNS`, `probe_claude_cli`, `claude_cli_path`, `claude_cli_timeout_s`.
- The factory passes `cli_path=` and `timeout_s=` (matching the constructor's keyword args). The `Config` field names (`claude_cli_path`, `claude_cli_timeout_s`) match what `ConfigOverrides` exposes and what `resolve_config` reads.
- The CLI flag names (`--claude-cli-path`, `--claude-cli-timeout`) match Task 20's wiring.

**Placeholder scan** — no "TBD", "TODO", "implement later", or vague directives. Every code step contains the actual code.
