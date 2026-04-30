# Adding an LLM provider

A "provider" is anything that implements `kuroi.providers.base.Provider`.
This guide walks through adding one, using the existing Ollama provider
as a reference.

## The Provider Protocol

```python
class Provider(Protocol):
    name: str   # e.g. "anthropic"
    model: str  # e.g. "claude-opus-4-7"

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        seed: int | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]: ...
```

Two attributes for identification, one method that takes word-indexed
pages and returns a flat list of `Finding`s plus per-chunk audit
records.

## Step 1: Write the client

Create `src/kuroi/providers/<name>.py`. Use `providers/ollama.py` as a
template — read it end-to-end first; it's about 150 lines and
demonstrates every required pattern.

Skeleton:

```python
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, serialize_for_llm
from kuroi.providers._shared import parse_findings_payload


SYSTEM_PROMPT = "..."        # static system prompt
OUTPUT_SCHEMA_HINT = "..."   # JSON schema the model must emit


def build_user_prompt(
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
) -> str:
    """Construct the user-message body sent to the model."""
    doc = serialize_for_llm(pages)
    cats = ", ".join(llm_category_ids) if llm_category_ids else "(none)"
    return (
        f"Active LLM categories: {cats}\n\n"
        f"Output schema: {OUTPUT_SCHEMA_HINT}\n\n"
        f"<document>\n{doc}\n</document>"
    )


class MyProvider:
    name = "myprovider"

    def __init__(
        self,
        *,
        model: str,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._client = client or httpx.Client(...)

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
        # ... call your model, get a JSON string back as `content` ...
        duration_ms = int((time.monotonic() - started) * 1000)
        response_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=seed is not None,  # True if your API honored it
            system_fingerprint=None,         # or whatever the API exposes
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            tokens_in=...,
            tokens_out=...,
            duration_ms=duration_ms,
        )

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return [], [chunk]
        if not isinstance(payload, dict):
            return [], [chunk]
        return (
            parse_findings_payload(payload, pages, source="llm"),
            [chunk],
        )
```

Use `providers/_shared.py:parse_findings_payload` — it validates
indices and confidences, drops hallucinated entries, and produces
canonical `Finding`s. Don't roll your own parser.

## Step 2: Register in the factory

Edit `src/kuroi/providers/factory.py` to dispatch your provider name:

```python
def make_provider(config: Config) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(model=config.model)
    if config.provider == "ollama":
        return OllamaProvider(model=config.model, url=config.ollama_url)
    if config.provider == "myprovider":
        return MyProvider(model=config.model)
    raise ValueError(f"Unknown provider: {config.provider!r}")
```

Then widen `ProviderName` and `VALID_PROVIDERS` in
`src/kuroi/core/config.py`:

```python
ProviderName = Literal["anthropic", "ollama", "myprovider"]
VALID_PROVIDERS: tuple[ProviderName, ...] = (
    "anthropic", "ollama", "myprovider",
)
```

If your provider needs new config keys (a base URL, a model alias),
follow the `[ollama]` table pattern in `core/config.py`: read the
value via `_read_string(file_data, "myprovider.url")`, add a CLI flag
in `cli/run.py`, and wire it through `ConfigOverrides`.

## Step 3: Add pricing

If your provider charges per token, add an entry to
`core/pricing.py` keyed by `(provider, model)` so
`kuroi.core.pricing.estimate_cost` reads correctly. Free / local
providers can leave the pricing table unchanged — `estimate_cost`
already handles missing rates.

## Step 4: Tests

Write `tests/providers/test_<name>.py` using
`tests/providers/test_ollama.py` as the template. The pattern: a small
stub class with a `post()` method that satisfies the `httpx.Client`
interface, seeded with a canned JSON response. Assert that
`detect_redactions` emits the expected `Finding`s, that the
`ChunkRecord` records prompt and response SHAs, and that error paths
(HTTP errors, JSON-decode errors, timeouts) return `[], [chunk]` (or
`[], []` when there are no LLM categories) with no findings.

```sh
$ make test
```

## Step 5: Update the docs

- Add a row to the provider comparison table in
  `docs/user-guide/providers.md`.
- Add a `=== "MyProvider"` tab to the "Configure a provider" section.
- Document any new config keys.

The CLI reference and Python API reference update automatically when
you re-run `make docs-build`.

## Worked example: Ollama

The shipping Ollama provider (`src/kuroi/providers/ollama.py`, ~150
lines) is the canonical reference. It shows how to:

- Build the system + user prompts with `<document>` tags that defang
  prompt-injection in extracted text.
- Wrap an `httpx.Client` with the right connect/read timeouts.
- Translate Ollama's `/api/chat` envelope (`prompt_eval_count`,
  `eval_count`) into the canonical `ChunkRecord` token counts.
- Hash prompts and responses with SHA-256 so the audit log captures
  reproducibility evidence.
- Fail closed: any HTTP, JSON, or schema error returns `([], [chunk])`
  without crashing the run.
