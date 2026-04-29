# Cluster 1 — Cost and reproducibility implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve gaps 1.1, 1.2, and 1.3 from the spec — add pricing-data-driven cost estimation, plumb `--seed` through providers with the honest-record contract, and remove the residual telemetry references from the design doc.

**Architecture:** A new `core/pricing.py` module owns a packaged `data/pricing.json`, provides `count_tokens()` (heuristic) and `estimate_cost()` (formula). The `Provider` protocol is extended to accept `seed` and to return per-call `ChunkRecord` metadata (prompt/response SHA, token counts, duration, seed-honored flag). `cli/run.py` consumes the metadata, displays pre-flight estimates and post-run divergence notes, and emits `chunk_request` audit events. A new `cli/config.py` Typer app gains a `refresh-pricing` subcommand that ingests an external pricing JSON.

**Tech Stack:** Python 3.12+, Typer, Rich, stdlib `hashlib`, stdlib `json`, pytest, mypy, ruff.

**Spec:** `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`, sections 1.1, 1.2, 1.3.

---

## File structure

**Created:**
- `src/kuroi/data/pricing.json` — shipped pricing entries keyed by `(provider, model)`.
- `src/kuroi/core/pricing.py` — `Pricing`, `load_pricing`, `count_tokens`, `estimate_cost`.
- `src/kuroi/core/audit_records.py` — `ChunkRecord` dataclass.
- `src/kuroi/cli/config.py` — `config_app` Typer app with `refresh-pricing` subcommand.
- `tests/core/test_pricing.py`
- `tests/cli/test_config.py`

**Modified:**
- `src/kuroi/providers/base.py` — extend `Provider` protocol: `seed` kwarg, return tuple.
- `src/kuroi/providers/anthropic.py` — plumb seed, build `ChunkRecord`.
- `src/kuroi/providers/ollama.py` — plumb seed, build `ChunkRecord`.
- `src/kuroi/cli/run.py` — `--seed` flag, pre-flight cost line, post-run divergence note, chunk_request audit events.
- `src/kuroi/cli/__init__.py` — register `config_app`.
- `pyproject.toml` — `[tool.hatch.build.targets.wheel.force-include]` entry for `data/pricing.json`.
- `tests/providers/test_anthropic.py`, `tests/providers/test_ollama.py`, `tests/providers/test_factory.py` — adapt to new provider signature.
- `tests/cli/test_run.py` — assertions for cost line and seed handling.
- `kuroi-design.md` — remove telemetry references (final task, doc-only).

---

## Task 1: Ship `data/pricing.json` and a load helper

**Why this task exists:** Every cost calculation downstream depends on per-`(provider, model)` rates. Centralizing this in a packaged JSON file (rather than constants) means future updates are a one-line edit, and `kuroi config refresh-pricing` (Task 11) has a clean target.

**Files:**
- Create: `src/kuroi/data/pricing.json`
- Create: `src/kuroi/core/pricing.py`
- Create: `tests/core/test_pricing.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_pricing.py`:

```python
"""Tests for the pricing module — load + lookup."""

import json
from pathlib import Path

import pytest

from kuroi.core.pricing import Pricing, ProviderRates, load_pricing


def test_load_pricing_reads_packaged_file() -> None:
    pricing = load_pricing()
    assert isinstance(pricing, Pricing)
    assert "anthropic" in pricing.providers
    assert "claude-opus-4-7" in pricing.providers["anthropic"]


def test_load_pricing_lookup_returns_rates() -> None:
    pricing = load_pricing()
    rates = pricing.rates("anthropic", "claude-opus-4-7")
    assert isinstance(rates, ProviderRates)
    assert rates.input_per_million > 0
    assert rates.output_per_million > 0


def test_load_pricing_unknown_model_raises() -> None:
    pricing = load_pricing()
    with pytest.raises(KeyError):
        pricing.rates("anthropic", "model-that-does-not-exist")


def test_load_pricing_from_explicit_path(tmp_path: Path) -> None:
    body = {
        "schema_version": 1,
        "updated_at": "2026-04-15",
        "providers": {
            "anthropic": {
                "claude-opus-4-7": {"input_per_million": 15.0, "output_per_million": 75.0}
            }
        },
    }
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(body))
    pricing = load_pricing(p)
    rates = pricing.rates("anthropic", "claude-opus-4-7")
    assert rates.input_per_million == 15.0
    assert rates.output_per_million == 75.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_pricing.py -v`
Expected: FAIL with `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Create the packaged pricing data**

Create `src/kuroi/data/pricing.json`:

```json
{
  "schema_version": 1,
  "updated_at": "2026-04-15",
  "providers": {
    "anthropic": {
      "claude-opus-4-7":   {"input_per_million": 15.00, "output_per_million": 75.00},
      "claude-sonnet-4-6": {"input_per_million":  3.00, "output_per_million": 15.00},
      "claude-haiku-4-5":  {"input_per_million":  0.80, "output_per_million":  4.00}
    },
    "ollama": {
      "*": {"input_per_million": 0.0, "output_per_million": 0.0}
    }
  }
}
```

The `"*"` key under `ollama` is a wildcard model entry — Ollama is local, so any model resolves to zero cost.

- [ ] **Step 4: Implement `pricing.py`**

Create `src/kuroi/core/pricing.py`:

```python
"""Pricing data + cost estimation.

Pricing entries ship with the package at `kuroi/data/pricing.json`. The file
is loaded once via `load_pricing()`. Per-provider/model rates are looked up
through `Pricing.rates()`. Token counts use a static character-based heuristic
(see `count_tokens`); costs are computed by `estimate_cost`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class ProviderRates:
    """Per-million-token USD rates for a single (provider, model)."""

    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class Pricing:
    """The full pricing table loaded from disk."""

    schema_version: int
    updated_at: str
    providers: dict[str, dict[str, ProviderRates]]

    def rates(self, provider: str, model: str) -> ProviderRates:
        """Look up rates. Falls back to the wildcard `"*"` entry if present."""
        provider_table = self.providers[provider]
        if model in provider_table:
            return provider_table[model]
        if "*" in provider_table:
            return provider_table["*"]
        raise KeyError(f"No pricing for {provider}/{model}")


def load_pricing(path: Path | None = None) -> Pricing:
    """Load pricing from `path` (for tests) or the packaged default."""
    if path is None:
        text = (files("kuroi") / "data" / "pricing.json").read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    providers: dict[str, dict[str, ProviderRates]] = {}
    for provider_name, models in raw["providers"].items():
        providers[provider_name] = {
            model_name: ProviderRates(
                input_per_million=float(rates["input_per_million"]),
                output_per_million=float(rates["output_per_million"]),
            )
            for model_name, rates in models.items()
        }
    return Pricing(
        schema_version=int(raw["schema_version"]),
        updated_at=str(raw["updated_at"]),
        providers=providers,
    )
```

- [ ] **Step 5: Wire the data file into the wheel**

Open `pyproject.toml`. Find the `[tool.hatch.build.targets.wheel.force-include]` block:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/kuroi/rules" = "kuroi/rules"
```

Add the data line:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/kuroi/rules" = "kuroi/rules"
"src/kuroi/data" = "kuroi/data"
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_pricing.py -v`
Expected: PASS, all four tests green.

- [ ] **Step 7: Commit**

```bash
git add src/kuroi/data/pricing.json src/kuroi/core/pricing.py tests/core/test_pricing.py pyproject.toml
git commit -m "feat(pricing): ship packaged pricing data and load helper"
```

---

## Task 2: Token counter heuristic

**Why this task exists:** Pre-flight cost estimation needs a token count without making a network call (since estimation runs before the user even confirms). A static character-based heuristic (1 token ≈ 4 chars for English-leaning content) is good enough; the post-run divergence note (Task 9) reports actual vs estimated so we know if the heuristic drifts.

**Files:**
- Modify: `src/kuroi/core/pricing.py`
- Modify: `tests/core/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_pricing.py`:

```python
from kuroi.core.pricing import count_tokens


def test_count_tokens_short_text() -> None:
    # 12 chars / 4 = 3, plus the 500-token overhead = 503.
    assert count_tokens("hello world!") == 503


def test_count_tokens_empty_text_includes_overhead() -> None:
    assert count_tokens("") == 500


def test_count_tokens_long_text_scales() -> None:
    text = "a" * 4000
    # 4000 / 4 = 1000 tokens, plus 500 overhead = 1500.
    assert count_tokens(text) == 1500
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_pricing.py::test_count_tokens_short_text -v`
Expected: FAIL with `ImportError: cannot import name 'count_tokens'`.

- [ ] **Step 3: Implement `count_tokens`**

Append to `src/kuroi/core/pricing.py`:

```python
PROMPT_OVERHEAD_TOKENS = 500
"""Static budget for system prompt + JSON schema + output schema hints.

This is a calibration parameter. The post-run divergence note logs cases where
the actual / estimated cost ratio exceeds 2.0 so this value can be tuned.
"""


def count_tokens(text: str) -> int:
    """Heuristic token count.

    Uses 4 chars/token (typical for English-leaning content across the major
    BPE tokenizers). Adds `PROMPT_OVERHEAD_TOKENS` for the kuroi-side prompt.
    """
    return len(text) // 4 + PROMPT_OVERHEAD_TOKENS
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/test_pricing.py -v`
Expected: PASS, all seven tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/pricing.py tests/core/test_pricing.py
git commit -m "feat(pricing): add count_tokens heuristic"
```

---

## Task 3: Cost estimator

**Why this task exists:** Combines pricing rates with token counts. The 0.18 output multiplier is fixed for v1.0 per spec section 1.1; tuning is a separate decision.

**Files:**
- Modify: `src/kuroi/core/pricing.py`
- Modify: `tests/core/test_pricing.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_pricing.py`:

```python
from kuroi.core.pricing import OUTPUT_MULTIPLIER, estimate_cost


def test_estimate_cost_anthropic_opus_one_thousand_tokens() -> None:
    pricing = load_pricing()
    # 1000 input tokens, 180 estimated output (0.18 × 1000).
    # Anthropic opus: 15.00/Mtok in, 75.00/Mtok out.
    # Cost = 1000/1e6 * 15 + 180/1e6 * 75 = 0.015 + 0.0135 = 0.0285
    cost = estimate_cost(pricing, "anthropic", "claude-opus-4-7", input_tokens=1000)
    assert cost == pytest.approx(0.0285, rel=1e-6)


def test_estimate_cost_ollama_is_zero() -> None:
    pricing = load_pricing()
    cost = estimate_cost(pricing, "ollama", "llama3.1:70b", input_tokens=10_000)
    assert cost == 0.0


def test_output_multiplier_constant() -> None:
    assert OUTPUT_MULTIPLIER == 0.18
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_pricing.py::test_estimate_cost_anthropic_opus_one_thousand_tokens -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `estimate_cost`**

Append to `src/kuroi/core/pricing.py`:

```python
OUTPUT_MULTIPLIER = 0.18
"""Static estimate that output tokens = input tokens × 0.18.

The JSON response is dominated by `(page, start, end)` integers and short
kind labels, empirically much smaller than the input. Frozen at v1.0;
calibration data goes through the post-run divergence note in cli/run.py.
"""


def estimate_cost(
    pricing: Pricing,
    provider: str,
    model: str,
    *,
    input_tokens: int,
) -> float:
    """Pre-flight cost estimate in USD."""
    rates = pricing.rates(provider, model)
    output_tokens = input_tokens * OUTPUT_MULTIPLIER
    return (
        input_tokens / 1_000_000 * rates.input_per_million
        + output_tokens / 1_000_000 * rates.output_per_million
    )
```

- [ ] **Step 4: Run all pricing tests**

Run: `uv run pytest tests/core/test_pricing.py -v`
Expected: PASS, all ten tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/pricing.py tests/core/test_pricing.py
git commit -m "feat(pricing): add estimate_cost with 0.18 output multiplier"
```

---

## Task 4: `ChunkRecord` dataclass

**Why this task exists:** Providers will need a structured carrier for per-LLM-call metadata (seed, sha, tokens, duration). `cli/run.py` then writes one `chunk_request` audit event per record. Lifting this into a small standalone module keeps the type out of `findings.py` (different concern) and makes future chunking work straightforward.

**Files:**
- Create: `src/kuroi/core/audit_records.py`
- Create: `tests/core/test_audit_records.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_audit_records.py`:

```python
from kuroi.core.audit_records import ChunkRecord


def test_chunk_record_is_frozen() -> None:
    rec = ChunkRecord(
        chunk_idx=0,
        pages=(1, 2, 3),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=1000,
        tokens_out=200,
        duration_ms=12000,
    )
    assert rec.pages == (1, 2, 3)
    assert rec.tokens_in == 1000


def test_chunk_record_seed_metadata() -> None:
    rec = ChunkRecord(
        chunk_idx=0,
        pages=(1,),
        temperature=0.0,
        seed_requested=42,
        seed_honored=True,
        system_fingerprint="fp_abc",
        prompt_sha256="0" * 64,
        response_sha256="1" * 64,
        tokens_in=10,
        tokens_out=2,
        duration_ms=100,
    )
    assert rec.seed_requested == 42
    assert rec.seed_honored is True
    assert rec.system_fingerprint == "fp_abc"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_audit_records.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `ChunkRecord`**

Create `src/kuroi/core/audit_records.py`:

```python
"""Per-LLM-call audit metadata.

`ChunkRecord` is what every Provider returns alongside its findings. The
fields map 1:1 to the `chunk_request` event schema in
docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md (section 2.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkRecord:
    """A single LLM-API call. There is one ChunkRecord per detect_redactions call;
    when chunking lands, providers will return a list with one entry per chunk."""

    chunk_idx: int
    pages: tuple[int, ...]
    temperature: float
    seed_requested: int | None
    seed_honored: bool
    system_fingerprint: str | None
    prompt_sha256: str
    response_sha256: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/core/test_audit_records.py -v`
Expected: PASS, both tests green.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/audit_records.py tests/core/test_audit_records.py
git commit -m "feat(audit): add ChunkRecord dataclass"
```

---

## Task 5: Update `Provider` protocol

**Why this task exists:** Providers must accept `seed` and return `ChunkRecord` data. This task only updates the protocol — concrete provider classes are updated in tasks 6 and 7.

**Files:**
- Modify: `src/kuroi/providers/base.py`

- [ ] **Step 1: Update the protocol**

Replace `src/kuroi/providers/base.py` with:

```python
"""The Provider Protocol every LLM client implements."""

from __future__ import annotations

from typing import Protocol

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page


class Provider(Protocol):
    """Interface kuroi uses to talk to any LLM, cloud or local."""

    name: str  # e.g. "anthropic"
    model: str  # e.g. "claude-opus-4-7"

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        seed: int | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]: ...
```

The breaking change: `detect_redactions` now takes a keyword-only `seed: int | None = None` and returns a tuple `(findings, chunks)` instead of a bare list.

- [ ] **Step 2: Type-check**

Run: `uv run mypy src/kuroi/providers/base.py`
Expected: no errors. (Concrete providers will fail mypy until the next two tasks; that's expected.)

- [ ] **Step 3: Commit**

```bash
git add src/kuroi/providers/base.py
git commit -m "refactor(providers): extend Provider protocol with seed and ChunkRecord"
```

---

## Task 6: `AnthropicProvider` returns `ChunkRecord`

**Why this task exists:** Wire seed plumbing and chunk metadata into the Anthropic implementation. The Anthropic SDK does not expose a `seed` parameter, so we record `seed_requested` truthfully and set `seed_honored=False`. We always set `temperature=0` when a seed is requested.

**Files:**
- Modify: `src/kuroi/providers/anthropic.py`
- Modify: `tests/providers/test_anthropic.py`

- [ ] **Step 1: Update the test**

Open `tests/providers/test_anthropic.py`. Locate the existing detect-redactions test (it currently asserts the return shape is `list[Finding]`). Replace its assertion block so it also exercises chunk metadata. If you don't have one yet, add this test:

```python
import hashlib
from unittest.mock import MagicMock

from kuroi.core.pdf import Page, Word
from kuroi.providers.anthropic import AnthropicProvider


def _stub_response(text: str, fingerprint: str | None = None, in_t: int = 100, out_t: int = 20) -> MagicMock:
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=in_t, output_tokens=out_t)
    response.system_fingerprint = fingerprint
    return response


def _one_page() -> tuple[Page, ...]:
    return (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Sarah", bbox=(0.0, 0.0, 10.0, 10.0)),
                Word(idx=1, text="Chen", bbox=(11.0, 0.0, 20.0, 10.0)),
            ),
        ),
    )


def test_anthropic_returns_findings_and_chunk_record() -> None:
    client = MagicMock()
    client.messages.create.return_value = _stub_response(
        '{"findings": [{"page": 1, "start": 0, "end": 1, "kind": "person_name", "confidence": "high"}]}',
        fingerprint=None,
        in_t=100,
        out_t=20,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert len(findings) == 1
    assert findings[0].kind == "person_name"
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_idx == 0
    assert chunk.pages == (1,)
    assert chunk.temperature == 0.0
    assert chunk.seed_requested is None
    assert chunk.seed_honored is False  # Anthropic SDK never honors a seed
    assert chunk.tokens_in == 100
    assert chunk.tokens_out == 20
    assert len(chunk.prompt_sha256) == 64
    assert len(chunk.response_sha256) == 64


def test_anthropic_records_seed_but_does_not_send_it() -> None:
    client = MagicMock()
    client.messages.create.return_value = _stub_response(
        '{"findings": []}', in_t=10, out_t=2
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    _, chunks = provider.detect_redactions(_one_page(), ("person_name",), seed=42)

    # seed is recorded, never sent.
    assert chunks[0].seed_requested == 42
    assert chunks[0].seed_honored is False
    call_kwargs = client.messages.create.call_args.kwargs
    assert "seed" not in call_kwargs
    assert call_kwargs["temperature"] == 0


def test_anthropic_no_categories_returns_empty_lists() -> None:
    client = MagicMock()
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)
    findings, chunks = provider.detect_redactions(_one_page(), ())
    assert findings == []
    assert chunks == []
    client.messages.create.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/providers/test_anthropic.py -v`
Expected: FAIL — current `detect_redactions` returns a bare `list`, no chunk record.

- [ ] **Step 3: Update the provider**

In `src/kuroi/providers/anthropic.py`, replace the `detect_redactions` method body and add the necessary imports. The full updated `detect_redactions`:

```python
import hashlib
import time

from kuroi.core.audit_records import ChunkRecord


def detect_redactions(
    self,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    seed: int | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
    if not llm_category_ids:
        return [], []
    user_prompt = build_user_prompt(pages, llm_category_ids)
    prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()

    started = time.monotonic()
    response = self._client.messages.create(
        model=self.model,
        max_tokens=self._max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0,  # always; --seed support is best-effort
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    response_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "input_tokens", 0)) if usage else 0
    tokens_out = int(getattr(usage, "output_tokens", 0)) if usage else 0

    chunk = ChunkRecord(
        chunk_idx=0,
        pages=tuple(p.number for p in pages),
        temperature=0.0,
        seed_requested=seed,
        seed_honored=False,  # Anthropic SDK does not expose seed
        system_fingerprint=getattr(response, "system_fingerprint", None),
        prompt_sha256=prompt_sha,
        response_sha256=response_sha,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
    )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [], [chunk]
    return parse_findings_payload(payload, pages, source="llm"), [chunk]
```

Be sure to add the `import hashlib` and `import time` at the top of the file (after the existing `import json`/`import os`), and `from kuroi.core.audit_records import ChunkRecord`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/providers/test_anthropic.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/kuroi/providers/anthropic.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/providers/anthropic.py tests/providers/test_anthropic.py
git commit -m "feat(providers): AnthropicProvider returns ChunkRecord, accepts seed"
```

---

## Task 7: `OllamaProvider` returns `ChunkRecord`

**Why this task exists:** Ollama supports `seed` and `temperature` natively. When seed is set, both are passed through and `seed_honored=True`.

**Files:**
- Modify: `src/kuroi/providers/ollama.py`
- Modify: `tests/providers/test_ollama.py`

- [ ] **Step 1: Update the test**

Add to `tests/providers/test_ollama.py`:

```python
from kuroi.core.pdf import Page, Word
from kuroi.providers.ollama import OllamaProvider


class _StubClient:
    def __init__(self, response_body: dict, prompt_eval: int = 100, eval_count: int = 20) -> None:
        self.response_body = response_body
        self.prompt_eval = prompt_eval
        self.eval_count = eval_count
        self.last_call_kwargs: dict | None = None

    def post(self, url, *, json):  # noqa: A002
        self.last_call_kwargs = {"url": url, "json": json}
        envelope = {
            "message": {"content": __import__("json").dumps(self.response_body)},
            "prompt_eval_count": self.prompt_eval,
            "eval_count": self.eval_count,
        }

        class R:
            def raise_for_status(self_inner):
                pass

            def json(self_inner):
                return envelope

        return R()


def _one_page() -> tuple[Page, ...]:
    return (
        Page(
            number=1,
            words=(Word(idx=0, text="Sarah", bbox=(0.0, 0.0, 10.0, 10.0)),),
        ),
    )


def test_ollama_returns_findings_and_chunk_record() -> None:
    client = _StubClient({"findings": []}, prompt_eval=42, eval_count=7)
    provider = OllamaProvider(model="llama3.1:70b", url="http://x", client=client)

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.tokens_in == 42
    assert chunk.tokens_out == 7
    assert chunk.seed_honored is False  # no seed requested
    assert chunk.seed_requested is None


def test_ollama_seed_is_sent_and_marked_honored() -> None:
    client = _StubClient({"findings": []})
    provider = OllamaProvider(model="llama3.1:70b", url="http://x", client=client)

    _, chunks = provider.detect_redactions(_one_page(), ("person_name",), seed=99)

    assert chunks[0].seed_requested == 99
    assert chunks[0].seed_honored is True
    body = client.last_call_kwargs["json"]
    assert body["options"]["seed"] == 99
    assert body["options"]["temperature"] == 0
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `uv run pytest tests/providers/test_ollama.py -v`
Expected: FAIL with attribute / signature errors.

- [ ] **Step 3: Update `OllamaProvider`**

Replace `detect_redactions` in `src/kuroi/providers/ollama.py`:

```python
import hashlib
import time

from kuroi.core.audit_records import ChunkRecord


def detect_redactions(
    self,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    seed: int | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
    if not llm_category_ids:
        return [], []
    user_prompt = build_user_prompt(pages, llm_category_ids)
    prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()

    options: dict[str, Any] = {"temperature": 0}
    if seed is not None:
        options["seed"] = seed
    body = {
        "model": self.model,
        "stream": False,
        "format": "json",
        "options": options,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    started = time.monotonic()
    try:
        response = self._client.post(f"{self._url}/api/chat", json=body)
        response.raise_for_status()
        envelope = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return [], []
    duration_ms = int((time.monotonic() - started) * 1000)

    message = envelope.get("message") if isinstance(envelope, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    response_sha = hashlib.sha256(
        (content or "").encode("utf-8")
    ).hexdigest()
    tokens_in = int(envelope.get("prompt_eval_count", 0)) if isinstance(envelope, dict) else 0
    tokens_out = int(envelope.get("eval_count", 0)) if isinstance(envelope, dict) else 0

    chunk = ChunkRecord(
        chunk_idx=0,
        pages=tuple(p.number for p in pages),
        temperature=0.0,
        seed_requested=seed,
        seed_honored=seed is not None,
        system_fingerprint=None,
        prompt_sha256=prompt_sha,
        response_sha256=response_sha,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
    )

    if not isinstance(content, str):
        return [], [chunk]
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return [], [chunk]
    if not isinstance(payload, dict):
        return [], [chunk]
    return parse_findings_payload(payload, pages, source="llm"), [chunk]
```

Add `import hashlib`, `import time`, and `from kuroi.core.audit_records import ChunkRecord` at the top of the file.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/providers/test_ollama.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check**

Run: `uv run mypy src/kuroi/providers/ollama.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/providers/ollama.py tests/providers/test_ollama.py
git commit -m "feat(providers): OllamaProvider returns ChunkRecord, plumbs seed"
```

---

## Task 8: Update `cli/run.py` for the new provider signature

**Why this task exists:** The provider signature changed in Tasks 5-7; `run.py` must unpack `(findings, chunks)` for the rest of the pipeline. We don't add the seed flag or cost display yet — Task 9 and 10 do those.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Run the existing test suite to confirm what breaks**

Run: `uv run pytest tests/ -x -q`
Expected: failures in `tests/cli/test_run.py` and possibly `tests/test_e2e.py` because the unpacking is wrong.

- [ ] **Step 2: Update the unpacking in `run.py`**

In `src/kuroi/cli/run.py`, find the line:

```python
findings.extend(provider.detect_redactions(pages, tuple(llm_cat_ids)))
```

Replace with:

```python
provider_findings, _chunks = provider.detect_redactions(pages, tuple(llm_cat_ids))
findings.extend(provider_findings)
```

The `_chunks` is unused in this task; Task 11 wires it to the audit log.

- [ ] **Step 3: Run the suite again**

Run: `uv run pytest tests/ -q`
Expected: PASS — the unpacking fix is sufficient to bring the suite back to green.

- [ ] **Step 4: Commit**

```bash
git add src/kuroi/cli/run.py
git commit -m "refactor(run): unpack new (findings, chunks) provider return"
```

---

## Task 9: Add `--seed` flag and the not-honored notice

**Why this task exists:** Surface the seed contract from the spec (1.2) at the CLI layer. When the resolved provider is Anthropic and `--seed` is given, kuroi prints the not-honored notice once before submitting.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_run.py`:

```python
import re

from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_run_seed_flag_anthropic_prints_not_honored_notice(
    monkeypatch, make_pdf, tmp_path
):
    """When --seed is set against Anthropic, kuroi prints a one-line notice."""
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Stub the SDK's Anthropic client so the run completes deterministically.
    from kuroi.providers import anthropic as ap

    class _StubResp:
        content = [type("B", (), {"text": '{"findings": []}'})()]
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
        system_fingerprint = None

    class _StubClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                return _StubResp()

    monkeypatch.setattr(ap, "AnthropicProvider", lambda **kw: ap.AnthropicProvider(
        client=_StubClient(), model=kw.get("model", "claude-opus-4-7")
    ))

    pdf = make_pdf(["dummy text"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(app, ["run", str(pdf), "-o", str(out), "--seed", "42", "-y"])
    assert "--seed recorded but only temperature=0 is enforced" in result.stdout
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run pytest tests/cli/test_run.py::test_run_seed_flag_anthropic_prints_not_honored_notice -v`
Expected: FAIL with `--seed: no such option` or similar.

- [ ] **Step 3: Add the flag and the notice**

In `src/kuroi/cli/run.py`:

After the existing `model` parameter, add:

```python
seed: int | None = typer.Option(
    None,
    "--seed",
    help="Reproducibility seed. Best-effort per provider; recorded in audit.",
),
```

Then, after `provider = make_provider(config)`, add:

```python
if seed is not None and provider.name == "anthropic":
    console.print(
        "  [yellow]note:[/] --seed recorded but only temperature=0 "
        "is enforced for this provider"
    )
```

Replace the existing `provider.detect_redactions(...)` call with:

```python
provider_findings, _chunks = provider.detect_redactions(
    pages, tuple(llm_cat_ids), seed=seed
)
findings.extend(provider_findings)
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/cli/test_run.py::test_run_seed_flag_anthropic_prints_not_honored_notice -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(run): add --seed flag with anthropic not-honored notice"
```

---

## Task 10: Pre-flight cost display + post-run divergence note

**Why this task exists:** Surfaces the cost contract from spec section 1.1 to the user. Estimate is shown before the confirmation prompt. After the run, if `actual / estimated > 2.0`, kuroi prints a calibration-drift note.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_run.py`:

```python
def test_run_displays_pre_flight_cost_estimate(monkeypatch, make_pdf, tmp_path):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from kuroi.providers import anthropic as ap

    class _StubResp:
        content = [type("B", (), {"text": '{"findings": []}'})()]
        usage = type("U", (), {"input_tokens": 100, "output_tokens": 20})()
        system_fingerprint = None

    class _StubClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                return _StubResp()

    real_provider = ap.AnthropicProvider
    monkeypatch.setattr(
        ap,
        "AnthropicProvider",
        lambda **kw: real_provider(client=_StubClient(), model=kw.get("model", "claude-opus-4-7")),
    )

    pdf = make_pdf(["short"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(app, ["run", str(pdf), "-o", str(out), "-y"])
    assert "Estimated cost" in result.stdout
    assert "$" in result.stdout
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run pytest tests/cli/test_run.py::test_run_displays_pre_flight_cost_estimate -v`
Expected: FAIL — no estimate line in output.

- [ ] **Step 3: Add the cost display**

In `src/kuroi/cli/run.py`:

Add imports near the top:

```python
from kuroi.core.pdf import serialize_for_llm
from kuroi.core.pricing import count_tokens, estimate_cost, load_pricing
```

Right after `pages = extract_word_index(pdf)`, before any LLM detection:

```python
pricing = load_pricing()
input_tokens = count_tokens(serialize_for_llm(pages))
estimated_cost = estimate_cost(
    pricing, provider_name=config.provider, model=config.model, input_tokens=input_tokens
)
console.print(
    f"  Estimated cost: ${estimated_cost:.4f}  "
    f"({input_tokens} input tokens × {config.provider}/{config.model})"
)
```

Wait — `estimate_cost`'s signature uses positional `provider, model`. Adjust:

```python
estimated_cost = estimate_cost(
    pricing, config.provider, config.model, input_tokens=input_tokens
)
```

Rename `_chunks` to `chunks` from Task 9 so it stays in scope for cost calculation:

```python
provider_findings, chunks = provider.detect_redactions(
    pages, tuple(llm_cat_ids), seed=seed
)
findings.extend(provider_findings)
```

Initialize `actual_cost = 0.0` immediately after — this guarantees the variable is bound on every code path, including verification failure (cluster 2 task 3 will read it from the failure branch too).

After the run completes successfully, before printing `Wrote {output}`:

```python
actual_in = sum(c.tokens_in for c in chunks)
actual_out = sum(c.tokens_out for c in chunks)
if actual_in > 0:
    rates = pricing.rates(config.provider, config.model)
    actual_cost = (
        actual_in / 1_000_000 * rates.input_per_million
        + actual_out / 1_000_000 * rates.output_per_million
    )
    if estimated_cost > 0 and actual_cost / estimated_cost > 2.0:
        console.print(
            "  [yellow]note:[/] cost estimate diverged from actual "
            f"(${estimated_cost:.4f} → ${actual_cost:.4f})"
        )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/cli/test_run.py -v`
Expected: PASS, both new tests green and existing tests still passing.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(run): show pre-flight cost estimate and divergence note"
```

---

## Task 11: Wire `chunk_request` audit events

**Why this task exists:** Each per-call `ChunkRecord` becomes an NDJSON line in the audit log so reproducibility data is recorded.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py` (new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_run.py`:

```python
import json


def test_run_writes_chunk_request_audit_event(monkeypatch, make_pdf, tmp_path):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    audit_dir = tmp_path / "audit"

    from kuroi.providers import anthropic as ap

    class _StubResp:
        content = [type("B", (), {"text": '{"findings": []}'})()]
        usage = type("U", (), {"input_tokens": 50, "output_tokens": 5})()
        system_fingerprint = None

    class _StubClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                return _StubResp()

    real_provider = ap.AnthropicProvider
    monkeypatch.setattr(
        ap,
        "AnthropicProvider",
        lambda **kw: real_provider(client=_StubClient(), model=kw.get("model", "claude-opus-4-7")),
    )

    pdf = make_pdf(["doc"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        ["run", str(pdf), "-o", str(out), "-y", "--audit-dir", str(audit_dir)],
    )
    assert result.exit_code == 0

    files = list(audit_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    chunk_lines = [
        json.loads(l) for l in lines if json.loads(l).get("event") == "chunk_request"
    ]
    assert len(chunk_lines) == 1
    assert chunk_lines[0]["tokens_in"] == 50
    assert chunk_lines[0]["tokens_out"] == 5
    assert chunk_lines[0]["seed_honored"] is False
```

- [ ] **Step 2: Run the test to verify failure**

Run: `uv run pytest tests/cli/test_run.py::test_run_writes_chunk_request_audit_event -v`
Expected: FAIL — no chunk_request event is emitted.

- [ ] **Step 3: Wire the events**

In `src/kuroi/cli/run.py`, after the audit log is opened (right after `audit = AuditLog.open(...)`) and before the redaction loop, write each chunk:

```python
from dataclasses import asdict

for chunk in chunks:
    audit.write_event("chunk_request", **asdict(chunk))
```

Add the `from dataclasses import asdict` import at the top.

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/cli/test_run.py::test_run_writes_chunk_request_audit_event -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(audit): write chunk_request events from run"
```

---

## Task 12: `kuroi config refresh-pricing` subcommand

**Why this task exists:** Spec section 1.1 specifies `kuroi config refresh-pricing` as the only path that updates `pricing.json`. The minimal viable form: read a user-provided pricing JSON, validate against the same loader as the packaged data, write atomically into the user's data directory.

**Files:**
- Create: `src/kuroi/cli/config.py`
- Create: `tests/cli/test_config.py`
- Modify: `src/kuroi/cli/__init__.py`
- Modify: `src/kuroi/core/pricing.py` (load order: user file > packaged)

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_config.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_config_refresh_pricing_writes_user_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    src = tmp_path / "new-pricing.json"
    src.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-05-01",
        "providers": {
            "anthropic": {
                "claude-opus-4-7": {"input_per_million": 99.0, "output_per_million": 199.0}
            }
        },
    }))

    result = runner.invoke(app, ["config", "refresh-pricing", "--from", str(src)])

    assert result.exit_code == 0
    user_file = tmp_path / "xdg-data" / "kuroi" / "pricing.json"
    assert user_file.exists()
    data = json.loads(user_file.read_text())
    assert data["providers"]["anthropic"]["claude-opus-4-7"]["input_per_million"] == 99.0


def test_config_refresh_pricing_rejects_invalid_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    src = tmp_path / "bad.json"
    src.write_text("not json at all")

    result = runner.invoke(app, ["config", "refresh-pricing", "--from", str(src)])

    assert result.exit_code != 0
```

- [ ] **Step 2: Run the tests to verify failure**

Run: `uv run pytest tests/cli/test_config.py -v`
Expected: FAIL — `kuroi config` doesn't exist.

- [ ] **Step 3: Implement the subcommand**

Create `src/kuroi/cli/config.py`:

```python
"""kuroi config — configuration management subcommands."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.pricing import load_pricing

config_app = typer.Typer(help="Configuration management.")
console = Console()


def xdg_data_home() -> Path:
    """`$XDG_DATA_HOME` or `~/.local/share`."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def user_pricing_path() -> Path:
    return xdg_data_home() / "kuroi" / "pricing.json"


@config_app.command("refresh-pricing")
def refresh_pricing(
    from_path: Path = typer.Option(
        ..., "--from", exists=True, dir_okay=False, readable=True,
        help="Path to a pricing.json file to install.",
    ),
) -> None:
    """Replace the user's pricing.json with the contents of `--from`.

    The file is validated by attempting to load it through the same parser the
    rest of kuroi uses; on validation failure, no changes are made.
    """
    try:
        load_pricing(from_path)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        console.print(f"[red]Invalid pricing file:[/] {exc}")
        raise typer.Exit(code=2) from exc

    target = user_pricing_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(from_path, tmp)
    os.replace(tmp, target)
    console.print(f"  Wrote {target}")
```

- [ ] **Step 4: Make `load_pricing` prefer the user file**

In `src/kuroi/core/pricing.py`, change `load_pricing` to look at the user file first:

```python
def load_pricing(path: Path | None = None) -> Pricing:
    """Load pricing.

    Resolution order: explicit `path` (for tests) > user override at
    `$XDG_DATA_HOME/kuroi/pricing.json` > packaged default.
    """
    if path is not None:
        text = path.read_text(encoding="utf-8")
    else:
        # Late import to avoid CLI dependency in core
        import os
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        user_file = Path(xdg) / "kuroi" / "pricing.json"
        if user_file.exists():
            text = user_file.read_text(encoding="utf-8")
        else:
            text = (files("kuroi") / "data" / "pricing.json").read_text(encoding="utf-8")

    raw = json.loads(text)
    # ... rest unchanged
```

- [ ] **Step 5: Register the subcommand**

In `src/kuroi/cli/__init__.py`, add the import and registration:

```python
from kuroi.cli.config import config_app

# ... after other add_typer calls:
app.add_typer(config_app, name="config", help="Configuration management.")
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/cli/test_config.py tests/core/test_pricing.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/kuroi/cli/config.py src/kuroi/cli/__init__.py src/kuroi/core/pricing.py tests/cli/test_config.py
git commit -m "feat(cli): add kuroi config refresh-pricing subcommand"
```

---

## Task 13: Remove telemetry references from `kuroi-design.md`

**Why this task exists:** Spec section 1.3 commits to removing telemetry from the design entirely. There is no code touched by this task — only the design document itself. The four spec-listed locations:

- `kuroi init` step 4 (lines ~242-248 in the original document — the "Anonymous usage statistics?" prompt)
- `[privacy] telemetry = false` line (~851)
- The "It does not phone home" sentence in section 8 (~1107)
- Any open-question framing about telemetry endpoints

**Files:**
- Modify: `kuroi-design.md`

- [ ] **Step 1: Read the current section structure**

Run: `grep -n -i "telemetry\|phone home\|usage statistics" kuroi-design.md`
Expected: lists the four locations.

- [ ] **Step 2: Edit `kuroi init` step 4**

Open `kuroi-design.md`. Find the section starting `4/4  Anonymous usage statistics?` (around line 242). Either delete the entire numbered step (renumbering the wizard from "4/4" to "3/3") or replace it with a different concluding step. Recommended: renumber the existing 3/3 (backups) into 3/3 final, and remove the telemetry step entirely.

After editing, confirm the wizard ends with the backups step.

- [ ] **Step 3: Edit the `[privacy]` config block**

Find the TOML excerpt near line 851:

```toml
[privacy]
audit-log         = true
audit-log-dir     = "~/.local/share/kuroi/audit"
telemetry         = false
```

Replace with:

```toml
[privacy]
audit-log         = true
audit-log-dir     = "~/.local/share/kuroi/audit"
```

- [ ] **Step 4: Edit section 8**

Find the bullet near line 1107 that begins `- It does not phone home.`. Replace with:

```
- It does not phone home. There is no telemetry, no opt-in usage statistics, no crash-report channel.
```

(Strengthens rather than removes — the sentence answers a real concern users have.)

- [ ] **Step 5: Verify the spec's expectations match the design now**

Run: `grep -n -i "telemetry" kuroi-design.md`
Expected: only the strengthened "no telemetry" sentence in section 8 remains.

- [ ] **Step 6: Commit**

```bash
git add kuroi-design.md
git commit -m "docs(design): remove telemetry references per resolution spec"
```

---

## Self-review checklist

After all 13 tasks are complete, run:

- [ ] `uv run pytest tests/ -q` — all tests pass.
- [ ] `uv run mypy src/` — no errors.
- [ ] `uv run ruff check src/ tests/` — no warnings.
- [ ] `kuroi run <fixture.pdf> -o out.pdf -y` — emits "Estimated cost: $X.XXXX" line.
- [ ] `kuroi run <fixture.pdf> -o out.pdf -y --seed 42` — emits the not-honored notice when provider is anthropic.
- [ ] Audit log file contains a `"event": "chunk_request"` line with `tokens_in`, `tokens_out`, `seed_honored`.
- [ ] `kuroi config refresh-pricing --from <new-pricing.json>` writes to `$XDG_DATA_HOME/kuroi/pricing.json`.
- [ ] No `grep -i telemetry kuroi-design.md` matches except the "no telemetry" line in section 8.
