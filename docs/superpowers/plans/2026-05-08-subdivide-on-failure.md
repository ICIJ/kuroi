# Subdivide-on-Failure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an LLM call fails after exhausting `RetryPolicy`, halve the batch (by pages, then by word range with a 50-word overlap) and recurse, instead of aborting the run. Stop subdividing at a hard floor (100 words on a single page) and abort with rich diagnostics. Align Anthropic and Ollama silent-data-loss paths with the chunker contract along the way.

**Architecture:** A new `_try_or_subdivide` helper in `core/chunking.py` wraps the existing per-batch retry loop. On full failure it calls `_halve(item)` (recursing on the two children) instead of raising immediately. A new `slice_page` helper in `core/pdf.py` produces re-indexed sub-pages. A new optional `page_word_range` field on `ChunkRecord` records sub-page slices in the audit log. Two error paths in `providers/anthropic.py` and three in `providers/ollama.py` change return value from `[], [chunk]` to `[], []` so the chunker treats them as "subdivide" signals.

**Tech Stack:** Python 3.12, dataclasses, pytest, mypy, ruff, uv.

---

## Spec Reference

Implements `docs/superpowers/specs/2026-05-08-subdivide-on-failure-design.md`.

---

## File Structure

**Modify (source):**
- `src/kuroi/core/pdf.py` — add `slice_page(page, word_start, word_end) -> Page`.
- `src/kuroi/core/audit_records.py` — add `page_word_range: tuple[int, int] | None = None` to `ChunkRecord` (at the end so existing positional/required fields stay required).
- `src/kuroi/core/chunking.py` — extend `BatchError` with diagnostic fields; add `_WorkItem`, `_IndexCounter`, `_translate_indices`, `_dedupe`, `_halve`, `_try_with_retries`, `_try_or_subdivide`; rewrite the body of `detect_redactions_chunked` to delegate per batch.
- `src/kuroi/providers/anthropic.py` — catch `BadRequestError("prompt is too long")`; change truncation path to return `[], []`.
- `src/kuroi/providers/ollama.py` — change three malformed-content paths from `[], [chunk]` to `[], []`.
- `src/kuroi/cli/run.py` — extend `BatchError` formatting to render the new floor diagnostics when present.

**Modify (tests):**
- `tests/core/test_pdf.py` — add `slice_page` tests.
- `tests/core/test_audit_records.py` — lock the new field's default and post-construction value.
- `tests/core/test_chunking.py` — replace the `chunks[0].chunk_idx == batch_idx` assertion with monotonicity; add `test_full_page_batch_emits_page_word_range_none`.
- `tests/providers/test_anthropic.py` — add three tests for the new failure-signal alignment.
- `tests/providers/test_ollama.py` — add three tests for malformed-content paths now returning `[], []`.

**Create (tests):**
- `tests/core/test_chunking_subdivision.py` — nine end-to-end subdivision tests with a `ScriptedFailureProvider` fixture.

**Modify (docs):**
- `CHANGELOG.md` — entries under `[Unreleased]`.
- `docs/user-guide/troubleshooting.md` — short paragraph on the new floor `BatchError`.
- `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md` — note the new optional `page_word_range` field on `chunk_request`.

---

## Tasks

### Task 1: `slice_page` helper

**Files:**
- Modify: `src/kuroi/core/pdf.py` (add new function below `extract_word_index`, before `serialize_for_llm`)
- Test: `tests/core/test_pdf.py` (append at the bottom)

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_pdf.py`:

```python
# ---------------------------------------------------------------------------
# slice_page tests
# ---------------------------------------------------------------------------


def test_slice_page_reindexes_words_to_zero_base() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = tuple(
        Word(idx=i, text=f"w{i}", bbox=(float(i), 0.0, float(i + 1), 1.0))
        for i in range(10)
    )
    page = Page(number=7, words=words)

    sliced = slice_page(page, 3, 7)

    assert tuple(w.idx for w in sliced.words) == (0, 1, 2, 3)
    assert tuple(w.text for w in sliced.words) == ("w3", "w4", "w5", "w6")


def test_slice_page_preserves_page_number() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = tuple(
        Word(idx=i, text=f"w{i}", bbox=(0.0, 0.0, 1.0, 1.0)) for i in range(5)
    )
    page = Page(number=42, words=words)

    sliced = slice_page(page, 1, 4)

    assert sliced.number == 42


def test_slice_page_preserves_word_text_and_bbox() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = (
        Word(idx=0, text="alpha", bbox=(0.0, 0.0, 5.0, 1.0)),
        Word(idx=1, text="beta", bbox=(5.0, 0.0, 10.0, 1.0)),
        Word(idx=2, text="gamma", bbox=(10.0, 0.0, 15.0, 1.0)),
    )
    page = Page(number=1, words=words)

    sliced = slice_page(page, 1, 3)

    assert sliced.words[0].text == "beta"
    assert sliced.words[0].bbox == (5.0, 0.0, 10.0, 1.0)
    assert sliced.words[1].text == "gamma"
    assert sliced.words[1].bbox == (10.0, 0.0, 15.0, 1.0)


def test_slice_page_full_range_round_trips_text() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = tuple(
        Word(idx=i, text=f"w{i}", bbox=(0.0, 0.0, 1.0, 1.0)) for i in range(3)
    )
    page = Page(number=1, words=words)

    sliced = slice_page(page, 0, 3)

    assert tuple(w.text for w in sliced.words) == ("w0", "w1", "w2")
    assert tuple(w.idx for w in sliced.words) == (0, 1, 2)


def test_slice_page_rejects_empty_range() -> None:
    import pytest

    from kuroi.core.pdf import Page, Word, slice_page

    page = Page(number=1, words=(Word(idx=0, text="x", bbox=(0.0, 0.0, 1.0, 1.0)),))

    with pytest.raises(ValueError, match="out of range"):
        slice_page(page, 0, 0)


def test_slice_page_rejects_negative_start() -> None:
    import pytest

    from kuroi.core.pdf import Page, Word, slice_page

    page = Page(number=1, words=(Word(idx=0, text="x", bbox=(0.0, 0.0, 1.0, 1.0)),))

    with pytest.raises(ValueError, match="out of range"):
        slice_page(page, -1, 1)


def test_slice_page_rejects_end_past_word_count() -> None:
    import pytest

    from kuroi.core.pdf import Page, Word, slice_page

    page = Page(
        number=1,
        words=(
            Word(idx=0, text="a", bbox=(0.0, 0.0, 1.0, 1.0)),
            Word(idx=1, text="b", bbox=(0.0, 0.0, 1.0, 1.0)),
        ),
    )

    with pytest.raises(ValueError, match="out of range"):
        slice_page(page, 0, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_pdf.py -k slice_page -v`
Expected: 7 failures (`ImportError: cannot import name 'slice_page'`).

- [ ] **Step 3: Implement `slice_page`**

In `src/kuroi/core/pdf.py`, add immediately after `extract_word_index` (around line 100, before `serialize_for_llm`):

```python
def slice_page(page: Page, word_start: int, word_end: int) -> Page:
    """Return a synthetic page containing words[word_start:word_end], with
    `Word.idx` re-indexed to 0..(word_end - word_start - 1).

    Re-indexing is what lets the synthetic page round-trip through the
    existing `serialize_for_llm` / `parse_findings_payload` pair without
    any other change: `parse_findings_payload` validates indices as
    positions in `page.words`, so resetting `idx` to 0..N-1 inside the
    slice keeps that invariant. The chunker translates the model's
    returned indices back to original-page coordinates by adding
    `word_start`.

    `page.number` is preserved, so findings naturally reference the
    original page in the document.

    Raises ValueError if the requested range is invalid (empty, negative,
    or past the end of the source page's word list).
    """
    if not (0 <= word_start < word_end <= len(page.words)):
        raise ValueError(
            f"slice {word_start}..{word_end} out of range for page "
            f"{page.number} ({len(page.words)} words)"
        )
    sliced = tuple(
        Word(idx=i, text=w.text, bbox=w.bbox)
        for i, w in enumerate(page.words[word_start:word_end])
    )
    return Page(number=page.number, words=sliced)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_pdf.py -k slice_page -v`
Expected: 7 passes.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py
git commit -m "$(cat <<'EOF'
feat(pdf): add slice_page helper for sub-page chunking

Returns a synthetic Page with words re-indexed to 0..N-1. Page number
is preserved so findings reference the original page; the chunker
translates returned indices back by adding the slice offset.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Extend `ChunkRecord` with `page_word_range`

**Files:**
- Modify: `src/kuroi/core/audit_records.py`
- Test: `tests/core/test_audit_records.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_audit_records.py`:

```python
def test_chunk_record_page_word_range_defaults_to_none() -> None:
    """Existing call sites that don't pass page_word_range still work."""
    from kuroi.core.audit_records import ChunkRecord

    record = ChunkRecord(
        chunk_idx=0,
        pages=(1,),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
    )

    assert record.page_word_range is None


def test_chunk_record_page_word_range_explicit_value_round_trips() -> None:
    from kuroi.core.audit_records import ChunkRecord

    record = ChunkRecord(
        chunk_idx=3,
        pages=(41,),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
        page_word_range=(3950, 8000),
    )

    assert record.page_word_range == (3950, 8000)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_audit_records.py -k page_word_range -v`
Expected: failures (`AttributeError: 'ChunkRecord' object has no attribute 'page_word_range'` or `TypeError` for the unexpected keyword).

- [ ] **Step 3: Add `page_word_range` to `ChunkRecord`**

In `src/kuroi/core/audit_records.py`, replace the existing `ChunkRecord` dataclass (lines 13–28):

```python
@dataclass(frozen=True)
class ChunkRecord:
    """A single LLM-API call. There is one ChunkRecord per detect_redactions call;
    the chunking orchestrator emits one ChunkRecord per successful sub-call when
    a batch is subdivided."""

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
    page_word_range: tuple[int, int] | None = None
```

The new field has a default, so all existing call sites in
`providers/anthropic.py`, `providers/ollama.py`, and the chunking tests
continue to work unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_audit_records.py -v`
Expected: all pass (the new ones plus all pre-existing tests).

Run the broader regression: `uv run pytest tests/ -v`
Expected: all pass (the field's default keeps everything green).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/audit_records.py tests/core/test_audit_records.py
git commit -m "$(cat <<'EOF'
feat(audit): add optional page_word_range to ChunkRecord

Defaults to None for full-page calls. Sub-page slices populate it with
the original-page (start, end) coordinates so the audit log faithfully
records subdivision.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Anthropic — catch `BadRequestError("prompt is too long")`

**Files:**
- Modify: `src/kuroi/providers/anthropic.py`
- Test: `tests/providers/test_anthropic.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_anthropic.py`:

```python
def test_anthropic_prompt_too_long_returns_empty_to_signal_subdivide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 'prompt is too long' BadRequestError is caught and translated to
    the chunker's subdivide signal: empty findings AND empty chunks."""
    import anthropic

    client = MagicMock()
    err = anthropic.BadRequestError(
        message="prompt is too long: 250000 tokens > 200000 maximum",
        response=MagicMock(),
        body={
            "error": {
                "type": "invalid_request_error",
                "message": "prompt is too long: 250000 tokens > 200000 maximum",
            }
        },
    )
    client.messages.create.side_effect = err
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    with caplog.at_level(logging.WARNING, logger="kuroi.providers.anthropic"):
        findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("prompt" in m.lower() and "too long" in m.lower() for m in warnings)


def test_anthropic_other_bad_request_errors_still_raise() -> None:
    """Non-size BadRequestErrors (malformed schema, unknown model, etc.)
    are real bugs; subdivision won't help, so they keep propagating."""
    import anthropic

    client = MagicMock()
    err = anthropic.BadRequestError(
        message="model: unknown-model is not a recognized model",
        response=MagicMock(),
        body={
            "error": {
                "type": "invalid_request_error",
                "message": "model: unknown-model is not a recognized model",
            }
        },
    )
    client.messages.create.side_effect = err
    provider = AnthropicProvider(model="unknown-model", client=client)

    with pytest.raises(anthropic.BadRequestError):
        provider.detect_redactions(_one_page(), ("person_name",))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_anthropic.py -k "prompt_too_long or other_bad_request" -v`
Expected: 2 failures — the first because the SDK exception propagates
out instead of being caught; the second may incidentally pass already
(uncaught exceptions do propagate today), but let's still let it ride
as a regression-lock for the catch logic in step 3.

- [ ] **Step 3: Catch the size error in the provider**

In `src/kuroi/providers/anthropic.py`, near the top (after `__all__`),
add a private helper:

```python
def _is_prompt_too_long(exc: object) -> bool:
    """True iff an Anthropic BadRequestError signals an oversized prompt.

    The SDK exposes the structured body on `exc.body`. We match on
    error.type == "invalid_request_error" *and* the substring
    "prompt is too long" anywhere in the message — narrower than catching
    every 400, which would swallow real misconfiguration (unknown model,
    malformed schema, etc.).
    """
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    if error.get("type") != "invalid_request_error":
        return False
    message = error.get("message", "")
    return isinstance(message, str) and "prompt is too long" in message.lower()
```

Then wrap the `messages.create` call inside `detect_redactions`. Locate
the existing block (around lines 91–98):

```python
        started = time.monotonic()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            **extra,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
```

Replace it with:

```python
        import anthropic  # local import to keep optional at module load

        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                **extra,
            )
        except anthropic.BadRequestError as exc:
            if _is_prompt_too_long(exc):
                logger.warning(
                    "anthropic rejected prompt as too long (will subdivide): %s",
                    exc,
                )
                return [], []
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_anthropic.py -v`
Expected: all pass (existing tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/anthropic.py tests/providers/test_anthropic.py
git commit -m "$(cat <<'EOF'
fix(anthropic): catch 'prompt is too long' as subdivide signal

Previously this BadRequestError propagated out of the provider and
crashed the run with a stack trace. Now it logs a WARNING and returns
empty findings AND empty chunks, which the chunker reads as
'subdivide this batch.' Other BadRequestErrors (malformed schema,
unknown model, etc.) still propagate — they're real bugs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Anthropic — truncation now signals subdivision

**Files:**
- Modify: `src/kuroi/providers/anthropic.py`
- Test: `tests/providers/test_anthropic.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_anthropic.py`:

```python
def test_anthropic_truncated_response_returns_empty_to_signal_subdivide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the model hits ~95% of max_tokens, the JSON is almost certainly
    cut off. Today this is a silent data-loss path (returns [chunk] with
    zero findings); now it must return ([], []) so the chunker subdivides
    and recovers the lost findings on a smaller prompt."""
    client = MagicMock()
    # max_tokens defaults to 4096; tokens_out=4000 is ~97.7%.
    client.messages.create.return_value = _stub_response(
        '{"findings": [{"page": 1, "start": 0, "end":',  # truncated mid-array
        in_t=100,
        out_t=4000,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client, max_tokens=4096)

    with caplog.at_level(logging.WARNING, logger="kuroi.providers.anthropic"):
        findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("truncat" in m.lower() for m in warnings)
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `uv run pytest tests/providers/test_anthropic.py -k truncated_response_returns_empty -v`
Expected: failure — today the provider returns `[], [chunk]` with the
chunk record, so `chunks == []` is False.

- [ ] **Step 3: Change the truncation path to return empty chunks**

In `src/kuroi/providers/anthropic.py`, locate the truncation warning
(around lines 117–125, in the body of `detect_redactions`):

```python
        if tokens_out and tokens_out >= int(self._max_tokens * _TRUNCATION_THRESHOLD):
            logger.warning(
                "anthropic response likely truncated: tokens_out=%d hit %.0f%% of "
                "max_tokens=%d. Increase max_tokens or split the document; the JSON "
                "is probably cut mid-array and findings will be lost.",
                tokens_out,
                100 * tokens_out / self._max_tokens,
                self._max_tokens,
            )
```

Replace with:

```python
        if tokens_out and tokens_out >= int(self._max_tokens * _TRUNCATION_THRESHOLD):
            logger.warning(
                "anthropic response truncated at max_tokens (will subdivide): "
                "tokens_out=%d hit %.0f%% of max_tokens=%d. The JSON was probably "
                "cut mid-array; subdividing this batch and retrying.",
                tokens_out,
                100 * tokens_out / self._max_tokens,
                self._max_tokens,
            )
            return [], []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_anthropic.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/anthropic.py tests/providers/test_anthropic.py
git commit -m "$(cat <<'EOF'
fix(anthropic): truncated response now signals subdivide instead of silent loss

Previously, when the response hit ~95% of max_tokens, the provider
emitted a chunk record with zero findings — a silent data-loss path
the chunker treated as 'success.' Now it returns empty findings and
empty chunks so the chunker subdivides the batch and recovers the
lost findings on smaller prompts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Ollama — malformed-content paths now signal subdivision

**Files:**
- Modify: `src/kuroi/providers/ollama.py`
- Test: `tests/providers/test_ollama.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_ollama.py`:

```python
def test_ollama_missing_message_content_returns_empty_chunks() -> None:
    """When the envelope is missing message.content, the call counts as a
    subdivide signal — empty findings AND empty chunks."""
    body = json.dumps({"done": True})
    client = _StubClient(response=_StubResponse(status_code=200, body=body))
    provider = OllamaProvider(
        model="llama3.1:8b",
        url="http://localhost:11434",
        client=client,  # type: ignore[arg-type]
    )

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []


def test_ollama_non_json_content_returns_empty_chunks() -> None:
    """`format=json` is honored at the HTTP level but the content body is
    not valid JSON. Same subdivide signal."""
    client = _StubClient(response=_ok_response("not actually json"))
    provider = OllamaProvider(
        model="llama3.1:8b",
        url="http://localhost:11434",
        client=client,  # type: ignore[arg-type]
    )

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []


def test_ollama_non_object_payload_returns_empty_chunks() -> None:
    """Content parses to a JSON array or scalar (not an object). Same."""
    client = _StubClient(response=_ok_response("[1, 2, 3]"))
    provider = OllamaProvider(
        model="llama3.1:8b",
        url="http://localhost:11434",
        client=client,  # type: ignore[arg-type]
    )

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []
```

Note: `tests/providers/test_ollama.py` already has
`test_missing_message_content_returns_empty_findings` (line 162) and
`test_non_json_body_returns_empty_findings` (line 152). Those assert
only on `findings == []`. The new tests above also assert `chunks ==
[]` — that's the new contract being enforced.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_ollama.py -k "returns_empty_chunks" -v`
Expected: 3 failures — today the provider returns `[], [chunk]`, so the
`chunks == []` assertion fails.

- [ ] **Step 3: Change all three malformed-content returns**

In `src/kuroi/providers/ollama.py`, locate the three return statements
(around lines 167–184). Currently:

```python
        if not isinstance(content, str):
            logger.warning(
                "ollama envelope missing message.content; got envelope keys: %r",
                list(envelope.keys()) if isinstance(envelope, dict) else type(envelope),
            )
            return [], [chunk]
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "ollama returned non-JSON content despite format=json (%s). First 500 chars: %r",
                exc,
                content[:500],
            )
            return [], [chunk]
        if not isinstance(payload, dict):
            logger.warning("ollama JSON payload is not an object (got %s)", type(payload).__name__)
            return [], [chunk]
```

Replace with:

```python
        if not isinstance(content, str):
            logger.warning(
                "ollama envelope missing message.content (will subdivide); got "
                "envelope keys: %r",
                list(envelope.keys()) if isinstance(envelope, dict) else type(envelope),
            )
            return [], []
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "ollama returned non-JSON content despite format=json (will "
                "subdivide; %s). First 500 chars: %r",
                exc,
                content[:500],
            )
            return [], []
        if not isinstance(payload, dict):
            logger.warning(
                "ollama JSON payload is not an object (will subdivide; got %s)",
                type(payload).__name__,
            )
            return [], []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_ollama.py -v`
Expected: all pass (the new ones plus pre-existing tests including the
two old `..._returns_empty_findings` tests, which still hold because
`findings == []` remains true).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/ollama.py tests/providers/test_ollama.py
git commit -m "$(cat <<'EOF'
fix(ollama): malformed-content paths now signal subdivide

Three response-content paths (missing message.content, non-JSON body
despite format=json, non-object JSON payload) previously emitted a
chunk record with zero findings — a silent data-loss path the chunker
treated as 'success.' Now they return empty findings and empty chunks
so the chunker subdivides and retries on smaller prompts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `_WorkItem` and `_IndexCounter` foundation

**Files:**
- Modify: `src/kuroi/core/chunking.py` (add new private types near the top, after the `BatchError` class)
- Test: `tests/core/test_chunking.py` (append at the bottom)

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_chunking.py`:

```python
# ---------------------------------------------------------------------------
# _WorkItem and _IndexCounter foundation tests
# ---------------------------------------------------------------------------


def test_workitem_from_pages_has_no_word_range() -> None:
    """Default constructor for full-page batches: word_range is None."""
    from kuroi.core.chunking import _WorkItem

    pages = (_page(1), _page(2), _page(3))
    item = _WorkItem.from_pages(pages)

    assert item.pages == pages
    assert item.word_range is None


def test_workitem_with_word_range_records_slice() -> None:
    """Sub-page work items carry the original-page coordinates."""
    from kuroi.core.chunking import _WorkItem

    page = _page(1)
    item = _WorkItem(pages=(page,), word_range=(10, 50))

    assert item.word_range == (10, 50)


def test_index_counter_starts_at_zero() -> None:
    from kuroi.core.chunking import _IndexCounter

    counter = _IndexCounter()
    assert counter.next() == 0
    assert counter.next() == 1
    assert counter.next() == 2


def test_index_counter_independent_instances() -> None:
    """Each run gets its own counter; they don't share state."""
    from kuroi.core.chunking import _IndexCounter

    a = _IndexCounter()
    b = _IndexCounter()
    a.next()
    a.next()
    assert b.next() == 0  # b is unaffected by a
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -k "workitem or index_counter" -v`
Expected: 4 failures (`ImportError` for `_WorkItem` and `_IndexCounter`).

- [ ] **Step 3: Add `_WorkItem` and `_IndexCounter` to `chunking.py`**

In `src/kuroi/core/chunking.py`, immediately after the existing `BatchError`
class (around line 47), add:

```python
@dataclass(frozen=True)
class _WorkItem:
    """One unit dispatched as a single Provider.detect_redactions call.

    For multi-page batches: pages is the full-page tuple, word_range is
    None. For sub-page slices: pages is a single synthetic (re-indexed)
    page produced by `slice_page`, and word_range is the
    (original_start, original_end) coordinates so the chunker can
    translate findings back and the audit log can record the slice.
    """

    pages: tuple[Page, ...]
    word_range: tuple[int, int] | None

    @classmethod
    def from_pages(cls, pages: tuple[Page, ...]) -> _WorkItem:
        return cls(pages=pages, word_range=None)


class _IndexCounter:
    """Globally-unique, monotonically-increasing chunk_idx generator.

    Threaded through recursion in _try_or_subdivide so that every
    successful sub-call (whether full-page or sliced) gets a unique
    chunk_idx in arrival order. Replaces the prior
    `chunk_idx = batch_idx` assignment, which loses uniqueness once a
    batch produces multiple sub-records.
    """

    __slots__ = ("_value",)

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        n = self._value
        self._value += 1
        return n
```

You will need to add `from dataclasses import dataclass` to the imports
at the top — `replace` is already imported, so update the line:

```python
from dataclasses import replace
```

to:

```python
from dataclasses import dataclass, replace
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -k "workitem or index_counter" -v`
Expected: 4 passes.

Run the broader regression: `uv run pytest tests/ -v`
Expected: all pass — `_WorkItem` and `_IndexCounter` are unused so far.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add _WorkItem and _IndexCounter foundation

Module-private types that subdivision will use. _WorkItem captures
either a full-page batch (word_range=None) or a sub-page slice
(word_range=(start,end) in original-page coordinates). _IndexCounter
gives every successful sub-call a globally-unique, monotonic
chunk_idx, replacing the prior chunk_idx=batch_idx mapping that loses
uniqueness when a batch subdivides.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `_translate_indices` helper

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_chunking.py`:

```python
# ---------------------------------------------------------------------------
# _translate_indices tests
# ---------------------------------------------------------------------------


def test_translate_indices_passthrough_for_full_page_item() -> None:
    """word_range=None: findings are returned unchanged."""
    from kuroi.core.chunking import _translate_indices, _WorkItem

    item = _WorkItem.from_pages((_page(5),))
    inputs = [
        Finding(page=5, start=3, end=4, kind="x", confidence="high", source="llm"),
    ]

    out = _translate_indices(inputs, item)

    assert out == inputs


def test_translate_indices_shifts_start_and_end_by_word_range_offset() -> None:
    """Sub-page slice with offset 200: the model returned (start=3, end=5);
    the original-page position is (start=203, end=205)."""
    from kuroi.core.chunking import _translate_indices, _WorkItem

    item = _WorkItem(pages=(_page(5),), word_range=(200, 400))
    inputs = [
        Finding(page=5, start=3, end=5, kind="x", confidence="high", source="llm"),
        Finding(page=5, start=10, end=10, kind="y", confidence="medium", source="llm"),
    ]

    out = _translate_indices(inputs, item)

    assert [(f.start, f.end) for f in out] == [(203, 205), (210, 210)]
    # Other fields unchanged
    assert out[0].kind == "x"
    assert out[0].confidence == "high"
    assert out[1].source == "llm"


def test_translate_indices_preserves_page_number() -> None:
    """The page field is already correct because slice_page preserves
    page.number; the helper must not touch it."""
    from kuroi.core.chunking import _translate_indices, _WorkItem

    item = _WorkItem(pages=(_page(7),), word_range=(100, 200))
    inputs = [
        Finding(page=7, start=0, end=1, kind="x", confidence="high", source="llm"),
    ]

    out = _translate_indices(inputs, item)

    assert out[0].page == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -k translate_indices -v`
Expected: 3 failures (`ImportError: cannot import name '_translate_indices'`).

- [ ] **Step 3: Implement `_translate_indices`**

In `src/kuroi/core/chunking.py`, immediately after `_IndexCounter`, add:

```python
def _translate_indices(findings: list[Finding], item: _WorkItem) -> list[Finding]:
    """Shift each finding's (start, end) into original-page coordinates.

    For full-page items (word_range is None) findings are returned
    unchanged. For sub-page items, add the slice's word_range[0] offset
    to start and end. The page number is already correct because
    `slice_page` preserves `Page.number`.
    """
    if item.word_range is None:
        return findings
    offset = item.word_range[0]
    return [replace(f, start=f.start + offset, end=f.end + offset) for f in findings]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -k translate_indices -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add _translate_indices for sub-page coordinates

Shifts a finding's (start, end) by the slice offset so sub-page
findings reference original-page word positions. No-op for full-page
work items. Page number is preserved by slice_page so this helper
only touches start/end.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `_dedupe` helper

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_chunking.py`:

```python
# ---------------------------------------------------------------------------
# _dedupe tests
# ---------------------------------------------------------------------------


def test_dedupe_collapses_identical_findings() -> None:
    """Same (page, start, end, kind) → keep one."""
    from kuroi.core.chunking import _dedupe

    a = Finding(page=1, start=10, end=12, kind="X", confidence="high", source="llm")
    b = Finding(page=1, start=10, end=12, kind="X", confidence="high", source="llm")

    out = _dedupe([a, b])

    assert len(out) == 1


def test_dedupe_keeps_highest_confidence_on_collision() -> None:
    """When dedupe key collides, the higher-confidence finding wins."""
    from kuroi.core.chunking import _dedupe

    high = Finding(page=1, start=0, end=0, kind="X", confidence="high", source="llm")
    low = Finding(page=1, start=0, end=0, kind="X", confidence="low", source="llm")

    out_a = _dedupe([low, high])
    out_b = _dedupe([high, low])

    assert len(out_a) == 1 and out_a[0].confidence == "high"
    assert len(out_b) == 1 and out_b[0].confidence == "high"


def test_dedupe_preserves_overlapping_non_identical_findings() -> None:
    """Overlapping ranges of the same kind are different findings; keep both
    so the redaction step naturally redacts the union."""
    from kuroi.core.chunking import _dedupe

    a = Finding(page=1, start=10, end=12, kind="X", confidence="high", source="llm")
    b = Finding(page=1, start=11, end=13, kind="X", confidence="high", source="llm")

    out = _dedupe([a, b])

    assert len(out) == 2


def test_dedupe_does_not_merge_across_kinds() -> None:
    """Same range, different kind → keep both (genuine disagreement)."""
    from kuroi.core.chunking import _dedupe

    person = Finding(page=1, start=0, end=1, kind="person_name", confidence="high", source="llm")
    email = Finding(page=1, start=0, end=1, kind="email", confidence="high", source="llm")

    out = _dedupe([person, email])

    assert len(out) == 2


def test_dedupe_preserves_order_of_first_seen() -> None:
    """Dedupe should be deterministic. Within a colliding key group, the
    earliest-arriving finding (after applying the highest-confidence rule)
    is kept; first-seen ordering across groups is preserved."""
    from kuroi.core.chunking import _dedupe

    f1 = Finding(page=1, start=0, end=0, kind="a", confidence="high", source="llm")
    f2 = Finding(page=1, start=1, end=1, kind="b", confidence="high", source="llm")
    f3 = Finding(page=1, start=0, end=0, kind="a", confidence="low", source="llm")

    out = _dedupe([f1, f2, f3])

    assert [(f.start, f.kind) for f in out] == [(0, "a"), (1, "b")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -k dedupe -v`
Expected: 5 failures (`ImportError: cannot import name '_dedupe'`).

- [ ] **Step 3: Implement `_dedupe`**

In `src/kuroi/core/chunking.py`, immediately after `_translate_indices`, add:

```python
_CONFIDENCE_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Collapse exact duplicates by (page, start, end, kind).

    On collision keep the higher-confidence record; ties go to the
    earliest-arriving record. Findings whose ranges *overlap* but are
    not identical are preserved on purpose — the redaction step then
    redacts the union, which is the safer outcome for a security tool.
    Findings of different kinds at the same range are likewise preserved
    (genuine disagreement worth surfacing in diff/verify).

    Order of returned findings is the order each unique key was first
    seen — keeps the output deterministic.
    """
    by_key: dict[tuple[int, int, int, str], int] = {}
    out: list[Finding] = []
    for f in findings:
        key = (f.page, f.start, f.end, f.kind)
        existing_idx = by_key.get(key)
        if existing_idx is None:
            by_key[key] = len(out)
            out.append(f)
            continue
        existing = out[existing_idx]
        if _CONFIDENCE_RANK.get(f.confidence, 0) > _CONFIDENCE_RANK.get(
            existing.confidence, 0
        ):
            out[existing_idx] = f
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -k dedupe -v`
Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add _dedupe for overlap-window collisions

Collapses identical (page, start, end, kind) findings keeping the
highest-confidence record. Overlapping-but-non-identical findings are
preserved — the redaction step naturally redacts the union, which is
the safer outcome for a security tool.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `_halve` — multi-page case

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_chunking.py`:

```python
# ---------------------------------------------------------------------------
# _halve tests
# ---------------------------------------------------------------------------


def test_halve_splits_multi_page_item_evenly() -> None:
    """4 pages → [[p1,p2], [p3,p4]]. Both children have word_range=None."""
    from kuroi.core.chunking import _halve, _WorkItem

    pages = tuple(_page(i) for i in range(1, 5))
    item = _WorkItem.from_pages(pages)

    left, right = _halve(item)

    assert tuple(p.number for p in left.pages) == (1, 2)
    assert tuple(p.number for p in right.pages) == (3, 4)
    assert left.word_range is None
    assert right.word_range is None


def test_halve_splits_multi_page_item_with_odd_count() -> None:
    """3 pages → [[p1], [p2,p3]]. Left half is the smaller half."""
    from kuroi.core.chunking import _halve, _WorkItem

    pages = (_page(1), _page(2), _page(3))
    item = _WorkItem.from_pages(pages)

    left, right = _halve(item)

    assert tuple(p.number for p in left.pages) == (1,)
    assert tuple(p.number for p in right.pages) == (2, 3)


def test_halve_two_page_item_yields_two_single_page_children() -> None:
    """2 pages is the smallest multi-page case: → [[p1], [p2]]."""
    from kuroi.core.chunking import _halve, _WorkItem

    item = _WorkItem.from_pages((_page(1), _page(2)))

    left, right = _halve(item)

    assert tuple(p.number for p in left.pages) == (1,)
    assert tuple(p.number for p in right.pages) == (2,)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -k _halve -v`
Expected: 3 failures (`ImportError: cannot import name '_halve'`).

- [ ] **Step 3: Add `MIN_CHUNK_WORDS` / `OVERLAP_WORDS` constants and `_halve` (multi-page only)**

In `src/kuroi/core/chunking.py`, near the top of the module (before the
`BatchError` class), add the constants:

```python
MIN_CHUNK_WORDS = 50
OVERLAP_WORDS = 50
```

Immediately after `_dedupe`, add `_halve` with the multi-page case
only (single-page and floor cases land in Tasks 10 and 11):

```python
def _halve(item: _WorkItem) -> tuple[_WorkItem, _WorkItem]:
    """Split a work item into two children for subdivision retry.

    Multi-page batches split by pages: `[p1, p2, p3, p4]` →
    `([p1, p2], [p3, p4])`. No overlap between halves — page boundaries
    are already meaningful boundaries in the document, so no entity can
    straddle them.

    Single-page and floor cases will be added in subsequent tasks.
    """
    if len(item.pages) >= 2:
        mid = len(item.pages) // 2
        left = _WorkItem.from_pages(item.pages[:mid])
        right = _WorkItem.from_pages(item.pages[mid:])
        return left, right
    raise NotImplementedError("single-page and floor cases come in Tasks 10–11")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -k _halve -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add _halve for multi-page subdivision

Splits multi-page WorkItems down the middle. No overlap — page
boundaries are already meaningful boundaries in the document, so no
entity can straddle them. Single-page slicing and floor handling come
next.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `_halve` — single-page case (with overlap)

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_chunking.py`:

```python
def _page_with_words(num: int, n_words: int) -> Page:
    return Page(
        number=num,
        words=tuple(
            Word(idx=i, text=f"w{i}", bbox=(float(i), 0.0, float(i + 1), 1.0))
            for i in range(n_words)
        ),
    )


def test_halve_single_page_produces_overlapping_halves() -> None:
    """1000-word page with M=500, OVERLAP=50:
    left  = words[0..550]   (sliced page has 550 words, idx 0..549)
    right = words[450..1000] (sliced page has 550 words, idx 0..549)
    Both word_ranges record the original-page coordinates.
    """
    from kuroi.core.chunking import _halve, OVERLAP_WORDS, _WorkItem

    page = _page_with_words(num=3, n_words=1000)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    expected_mid = 500
    assert left.word_range == (0, expected_mid + OVERLAP_WORDS)
    assert right.word_range == (expected_mid - OVERLAP_WORDS, 1000)
    # Both halves carry exactly one page (the sliced page).
    assert len(left.pages) == 1 and len(right.pages) == 1
    # Sliced pages preserve page.number
    assert left.pages[0].number == 3
    assert right.pages[0].number == 3
    # Sliced word counts match the ranges
    assert len(left.pages[0].words) == expected_mid + OVERLAP_WORDS
    assert len(right.pages[0].words) == 1000 - (expected_mid - OVERLAP_WORDS)


def test_halve_single_page_overlap_includes_original_text_at_boundary() -> None:
    """The overlap zone (M-O .. M+O) must appear in the sliced text of both
    halves, so a boundary-straddling entity survives in at least one."""
    from kuroi.core.chunking import _halve, OVERLAP_WORDS, _WorkItem

    page = _page_with_words(num=1, n_words=200)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    # Original M=100, OVERLAP=50, overlap zone is original words[50..150]
    boundary_texts = {f"w{i}" for i in range(100 - OVERLAP_WORDS, 100 + OVERLAP_WORDS)}
    left_texts = {w.text for w in left.pages[0].words}
    right_texts = {w.text for w in right.pages[0].words}

    assert boundary_texts.issubset(left_texts)
    assert boundary_texts.issubset(right_texts)


def test_halve_single_page_re_indexes_words_to_zero_base() -> None:
    """The sliced page's Word.idx values restart at 0 in each child."""
    from kuroi.core.chunking import _halve, _WorkItem

    page = _page_with_words(num=1, n_words=300)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    assert left.pages[0].words[0].idx == 0
    assert right.pages[0].words[0].idx == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -k "halve_single_page" -v`
Expected: 3 failures with `NotImplementedError` from the placeholder.

- [ ] **Step 3: Add the single-page case to `_halve`**

In `src/kuroi/core/chunking.py`, replace the body of `_halve`:

```python
def _halve(item: _WorkItem) -> tuple[_WorkItem, _WorkItem]:
    """Split a work item into two children for subdivision retry.

    Multi-page batches split by pages: `[p1, p2, p3, p4]` →
    `([p1, p2], [p3, p4])`. No overlap — page boundaries are already
    meaningful boundaries in the document.

    Single-page work items (or sub-page slices that have grown long
    enough to warrant another split) split by word range with a
    symmetric OVERLAP_WORDS overlap so entities straddling the cut
    survive in at least one half.

    Floor handling (raising BatchError when no further halving makes
    sense) lands in Task 11.
    """
    if len(item.pages) >= 2:
        mid = len(item.pages) // 2
        left = _WorkItem.from_pages(item.pages[:mid])
        right = _WorkItem.from_pages(item.pages[mid:])
        return left, right

    # Single page (full or already-sliced).
    page = item.pages[0]
    base_start = item.word_range[0] if item.word_range is not None else 0
    base_end = item.word_range[1] if item.word_range is not None else len(page.words)
    n = base_end - base_start
    mid = n // 2

    # Boundaries inside the *current* page-slice (0..n).
    left_local_end = mid + OVERLAP_WORDS
    right_local_start = mid - OVERLAP_WORDS

    # The page object inside `item` is already (full or) sliced; we slice
    # *it again* to produce children. For full-page items, that's the
    # original page. For already-sliced items, that's the synthetic page.
    left_page = slice_page(page, 0, left_local_end)
    right_page = slice_page(page, right_local_start, n)

    left = _WorkItem(
        pages=(left_page,),
        word_range=(base_start, base_start + left_local_end),
    )
    right = _WorkItem(
        pages=(right_page,),
        word_range=(base_start + right_local_start, base_end),
    )
    return left, right
```

You also need to import `slice_page` near the top:

```python
from kuroi.core.pdf import Page, slice_page
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -k _halve -v`
Expected: all 6 `_halve` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add single-page word-range halving with overlap

Single-page WorkItems halve at the midpoint with a 50-word symmetric
overlap on each side. Both halves cover the boundary zone, so any
entity straddling the cut appears in at least one half — dedupe
collapses double-coverage findings later.

The page object passed in may already be a slice (when subdividing
recursively); `_halve` slices it again relative to its current word
range and tracks original-page coordinates in word_range.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Floor case + extended `BatchError`

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_chunking.py`:

```python
# ---------------------------------------------------------------------------
# _halve floor + BatchError diagnostics
# ---------------------------------------------------------------------------


def test_halve_raises_at_floor_for_too_small_single_page() -> None:
    """N <= 2 * OVERLAP_WORDS → no useful subdivision possible."""
    import pytest

    from kuroi.core.chunking import _halve, BatchError, _WorkItem

    page = _page_with_words(num=1, n_words=80)  # 80 <= 2*50
    item = _WorkItem.from_pages((page,))

    with pytest.raises(BatchError) as exc_info:
        _halve(item)

    err = exc_info.value
    assert err.last_failed_word_range == (0, 80)


def test_halve_raises_at_floor_when_halves_would_be_too_small() -> None:
    """N=99, OVERLAP=50: mid=49, left would be 99 words, right would be
    99 words — neither is strictly smaller than the parent, so floor."""
    import pytest

    from kuroi.core.chunking import _halve, BatchError, _WorkItem

    page = _page_with_words(num=1, n_words=99)
    item = _WorkItem.from_pages((page,))

    with pytest.raises(BatchError):
        _halve(item)


def test_halve_does_not_raise_just_above_the_floor() -> None:
    """N=101, OVERLAP=50: mid=50, halves are 100 words each — strictly
    smaller than 101. Floor not reached."""
    from kuroi.core.chunking import _halve, _WorkItem

    page = _page_with_words(num=1, n_words=101)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    assert len(left.pages[0].words) == 100
    assert len(right.pages[0].words) == 100


def test_batch_error_carries_subdivision_diagnostics_when_set() -> None:
    """The new optional fields default cleanly when not set, and round-trip
    when explicitly populated."""
    from kuroi.core.chunking import BatchError

    err = BatchError(
        batch_idx=4,
        page_numbers=(41,),
        attempts=3,
        subdivision_levels=7,
        last_failed_word_range=(0, 100),
        last_prompt_chars=4823,
    )

    assert err.subdivision_levels == 7
    assert err.last_failed_word_range == (0, 100)
    assert err.last_prompt_chars == 4823
    msg = str(err)
    assert "page 41" in msg
    assert "100 words" in msg
    assert "Tried 7 levels" in msg


def test_batch_error_legacy_message_for_multi_page_failure() -> None:
    """When subdivision diagnostics aren't set (e.g. simple multi-page
    BatchError surfaced from the retry path), keep the old single-line
    message for backward compatibility with existing CLI handling."""
    from kuroi.core.chunking import BatchError

    err = BatchError(batch_idx=3, page_numbers=(13, 14, 15), attempts=3)

    msg = str(err)
    assert msg == "Batch 4 (pages 13–15) failed 3 times and was aborted."  # noqa: RUF001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -k "floor or batch_error_carries or batch_error_legacy" -v`
Expected: 5 failures (the existing `BatchError` doesn't take the new
keyword args; `_halve` returns successfully on tiny inputs because
`slice_page` rejects empty slices but the floor check isn't there yet).

- [ ] **Step 3: Extend `BatchError` and add the floor case to `_halve`**

In `src/kuroi/core/chunking.py`, replace the existing `BatchError` class
(currently at lines ~32–47):

```python
class BatchError(Exception):
    """Raised when a batch fails every attempt (initial call + retries),
    optionally after exhausting all subdivision levels.

    The legacy single-line message
    `"Batch N (pages X–Y) failed K times and was aborted."` is preserved
    for the multi-page case where subdivision diagnostics aren't
    populated. The richer multi-line message is used when subdivision
    *did* run and bottomed out at the floor.
    """

    def __init__(
        self,
        batch_idx: int,
        page_numbers: tuple[int, ...],
        attempts: int,
        *,
        subdivision_levels: int = 0,
        last_failed_word_range: tuple[int, int] | None = None,
        last_prompt_chars: int = 0,
    ) -> None:
        self.batch_idx = batch_idx
        self.page_numbers = page_numbers
        self.attempts = attempts
        self.subdivision_levels = subdivision_levels
        self.last_failed_word_range = last_failed_word_range
        self.last_prompt_chars = last_prompt_chars
        if subdivision_levels > 0 and last_failed_word_range is not None:
            page_label = page_numbers[0] if len(page_numbers) == 1 else _format_page_range(
                page_numbers
            )
            start, end = last_failed_word_range
            n_words = end - start
            super().__init__(
                f"page {page_label} ({n_words} words) could not be processed "
                f"even after subdividing to the minimum chunk size "
                f"({2 * OVERLAP_WORDS} words).\n\n"
                f"Tried {subdivision_levels} levels of subdivision. The last "
                f"failed slice was page {page_label} words {start}..{end} "
                f"(prompt_chars={last_prompt_chars}, attempts={attempts}).\n\n"
                f"Likely causes:\n"
                f"  - The model can't keep up with this prompt size in the "
                f"available timeout\n"
                f"  - Output truncation: the model has more findings than "
                f"max_tokens allows\n"
                f"  - The page contains content the model rejects "
                f"(e.g., policy refusals)\n\n"
                f"Suggestions:\n"
                f"  - Use a model with larger context / faster throughput\n"
                f"  - Increase the retry budget: --max-retries 5"
            )
        else:
            super().__init__(
                f"Batch {batch_idx + 1} (pages {_format_page_range(page_numbers)}) "
                f"failed {attempts} times and was aborted."
            )
```

Then replace the entire `_halve` function body with the floor-aware
version:

```python
def _halve(item: _WorkItem) -> tuple[_WorkItem, _WorkItem]:
    """Split a work item into two children for subdivision retry.

    Multi-page batches split by pages: `[p1, p2, p3, p4]` →
    `([p1, p2], [p3, p4])`. No overlap — page boundaries are already
    meaningful boundaries in the document.

    Single-page work items (or sub-page slices that have grown long
    enough to warrant another split) split by word range with a
    symmetric OVERLAP_WORDS overlap so entities straddling the cut
    survive in at least one half.

    Raises BatchError when the floor is reached: a halving step that
    would not produce children strictly smaller than the parent. With
    OVERLAP_WORDS=50, the effective floor is N <= 100 words on a single
    page. The caller (`_try_or_subdivide`) catches this and re-raises
    with full diagnostics; we raise without diagnostics here and let
    the caller fill them in.
    """
    if len(item.pages) >= 2:
        mid = len(item.pages) // 2
        left = _WorkItem.from_pages(item.pages[:mid])
        right = _WorkItem.from_pages(item.pages[mid:])
        return left, right

    page = item.pages[0]
    base_start = item.word_range[0] if item.word_range is not None else 0
    base_end = item.word_range[1] if item.word_range is not None else len(page.words)
    n = base_end - base_start

    # Floor check: each child has size n//2 + OVERLAP_WORDS. For the children
    # to be strictly smaller than the parent we need n//2 + OVERLAP_WORDS < n,
    # which simplifies to n > 2*OVERLAP_WORDS. We also require children to be
    # at least MIN_CHUNK_WORDS so we don't dispatch trivially-tiny prompts.
    if n <= 2 * OVERLAP_WORDS or n // 2 + OVERLAP_WORDS < MIN_CHUNK_WORDS:
        raise BatchError(
            batch_idx=0,  # filled in by _try_or_subdivide
            page_numbers=(page.number,),
            attempts=0,  # filled in by _try_or_subdivide
            subdivision_levels=1,
            last_failed_word_range=(base_start, base_end),
        )

    mid = n // 2
    left_local_end = mid + OVERLAP_WORDS
    right_local_start = mid - OVERLAP_WORDS

    left_page = slice_page(page, 0, left_local_end)
    right_page = slice_page(page, right_local_start, n)

    left = _WorkItem(
        pages=(left_page,),
        word_range=(base_start, base_start + left_local_end),
    )
    right = _WorkItem(
        pages=(right_page,),
        word_range=(base_start + right_local_start, base_end),
    )
    return left, right
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: all pass — including the original chunking suite and the new
floor + diagnostics tests.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add subdivision floor + extend BatchError diagnostics

_halve now raises BatchError when the floor is reached: N <= 2 *
OVERLAP_WORDS on a single page, or halving wouldn't produce strictly
smaller children. BatchError gains optional subdivision_levels,
last_failed_word_range, and last_prompt_chars fields; when set, the
error message includes a richer 'page N (M words) could not be
processed even after subdividing...' diagnostic. The legacy
single-line message is preserved for multi-page failures.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Extract `_try_with_retries` and add `_try_or_subdivide`

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking.py`

This is the wiring task: replace the inlined retry loop in
`detect_redactions_chunked` with a call to `_try_or_subdivide(item, ...)`,
which in turn calls `_try_with_retries(...)` and falls back to `_halve`
on failure. Existing chunking tests must continue to pass; new
end-to-end subdivision tests come in Task 13.

- [ ] **Step 1: Add the new audit-shape regression test**

The existing `test_chunked_call_renumbers_chunk_idx_to_batch_position`
(at `tests/core/test_chunking.py:194`) already asserts
`[c.chunk_idx for c in chunks] == [0, 1, 2]`. Under the new global
counter that assertion is still correct: for runs without subdivision,
the counter produces the same `0, 1, …, total_batches − 1` sequence.
**No change to the existing test.** Just leave it alone — it now
regression-locks the no-subdivision happy path.

Append a new test that locks the default audit shape (full-page calls
must report `page_word_range = None`):

```python
def test_chunked_call_emits_page_word_range_none_for_full_page_batches() -> None:
    """Full-page calls record page_word_range=None — no false 'subdivided'
    signals in the audit log when the run was healthy."""
    from kuroi.core.chunking import detect_redactions_chunked

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(scripts=[([], [_chunk((1,))]), ([], [_chunk((2,))])])

    _, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert all(c.page_word_range is None for c in chunks)
```

- [ ] **Step 2: Run the existing chunking tests to confirm they currently pass against the old implementation**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: all pass (the new monotonicity assertion still passes today;
the new `page_word_range is None` test passes because the field default
is `None`).

- [ ] **Step 3: Refactor `detect_redactions_chunked`**

In `src/kuroi/core/chunking.py`, replace the entire body of
`detect_redactions_chunked` (currently roughly lines 50–117) with:

```python
def detect_redactions_chunked(
    provider: Provider,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    pages_per_batch: int,
    retry_policy: RetryPolicy,
    on_batch_start: Callable[[int, int, tuple[int, ...]], None] | None = None,
    on_batch_complete: Callable[[int, int, tuple[int, ...], ChunkRecord], None] | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
    if pages_per_batch < 1:
        raise ValueError(f"pages_per_batch must be >= 1, got {pages_per_batch}")

    total_batches = math.ceil(len(pages) / pages_per_batch)
    aggregate_findings: list[Finding] = []
    aggregate_chunks: list[ChunkRecord] = []
    counter = _IndexCounter()

    for batch_idx in range(total_batches):
        offset = batch_idx * pages_per_batch
        batch = pages[offset : offset + pages_per_batch]
        page_numbers = tuple(p.number for p in batch)

        if on_batch_start is not None:
            on_batch_start(batch_idx, total_batches, page_numbers)

        item = _WorkItem.from_pages(batch)
        try:
            findings, chunks = _try_or_subdivide(
                item,
                provider,
                llm_category_ids,
                instructions=instructions,
                seed=seed,
                retry_policy=retry_policy,
                counter=counter,
                batch_idx=batch_idx,
                subdivision_level=0,
            )
        except BatchError as exc:
            # _halve raised at floor with batch_idx=0/attempts=0; replace
            # those with the real values from this top-level batch.
            raise BatchError(
                batch_idx=batch_idx,
                page_numbers=exc.page_numbers,
                attempts=len(retry_policy.schedule()) + 1,
                subdivision_levels=exc.subdivision_levels,
                last_failed_word_range=exc.last_failed_word_range,
                last_prompt_chars=exc.last_prompt_chars,
            ) from exc

        aggregate_findings.extend(findings)
        aggregate_chunks.extend(chunks)

        if on_batch_complete is not None and chunks:
            on_batch_complete(batch_idx, total_batches, page_numbers, chunks[-1])

    return aggregate_findings, aggregate_chunks


def _try_with_retries(
    item: _WorkItem,
    provider: Provider,
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...],
    seed: int | None,
    retry_policy: RetryPolicy,
    batch_idx: int,
) -> tuple[list[Finding], list[ChunkRecord]]:
    """Run the configured retry loop for a single work item.

    Returns whatever the provider returns on the last successful call
    (chunks non-empty), or `[], []` if every attempt produced empty
    chunks. The caller (`_try_or_subdivide`) decides what to do with
    `[], []` — that's the "subdivide" signal.
    """
    schedule = retry_policy.schedule()
    total_attempts = len(schedule) + 1
    page_numbers = tuple(p.number for p in item.pages)

    for attempt in range(total_attempts):
        findings, chunks = provider.detect_redactions(
            item.pages,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
            attempt=attempt,
        )
        if chunks:
            assert len(chunks) == 1, (
                f"providers must return exactly one ChunkRecord per call, "
                f"got {len(chunks)}"
            )
            return findings, chunks
        if attempt + 1 >= total_attempts:
            return [], []
        backoff = schedule[attempt]
        logger.info(
            "retrying batch %d (pages %s) in %.0fs (attempt %d/%d)",
            batch_idx + 1,
            _format_page_range(page_numbers),
            backoff,
            attempt + 2,
            total_attempts,
        )
        time.sleep(backoff)
    return [], []


def _try_or_subdivide(
    item: _WorkItem,
    provider: Provider,
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...],
    seed: int | None,
    retry_policy: RetryPolicy,
    counter: _IndexCounter,
    batch_idx: int,
    subdivision_level: int,
) -> tuple[list[Finding], list[ChunkRecord]]:
    """Try a work item; on full-retry failure, subdivide and recurse.

    Subdivision is *post-retry*: transient flakes get the full retry
    budget at every recursion level. Only after retries are exhausted
    do we conclude "this batch is too big" and split.

    Each recursive call increments subdivision_level so a floor-failed
    BatchError can report the depth at which it bottomed out.
    """
    findings, chunks = _try_with_retries(
        item,
        provider,
        llm_category_ids,
        instructions=instructions,
        seed=seed,
        retry_policy=retry_policy,
        batch_idx=batch_idx,
    )
    if chunks:
        translated = _translate_indices(findings, item)
        renumbered = [
            replace(c, chunk_idx=counter.next(), page_word_range=item.word_range)
            for c in chunks
        ]
        return translated, renumbered

    # Empty chunks → subdivide. _halve raises BatchError at the floor.
    try:
        left, right = _halve(item)
    except BatchError as exc:
        # Annotate the floor-raised error with the recursion depth that
        # got us here. last_failed_word_range and last_prompt_chars are
        # already set by _halve; we add the level count.
        raise BatchError(
            batch_idx=exc.batch_idx,
            page_numbers=exc.page_numbers,
            attempts=exc.attempts,
            subdivision_levels=subdivision_level + 1,
            last_failed_word_range=exc.last_failed_word_range,
            last_prompt_chars=exc.last_prompt_chars,
        ) from exc

    out_f: list[Finding] = []
    out_c: list[ChunkRecord] = []
    for child in (left, right):
        f, c = _try_or_subdivide(
            child,
            provider,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
            retry_policy=retry_policy,
            counter=counter,
            batch_idx=batch_idx,
            subdivision_level=subdivision_level + 1,
        )
        out_f.extend(f)
        out_c.extend(c)

    return _dedupe(out_f), out_c
```

The old `assert len(renumbered) == 1` and the inlined retry loop are
gone — the per-call invariant lives inside `_try_with_retries` now,
and `_try_or_subdivide` accumulates across multiple sub-calls.

- [ ] **Step 4: Run tests to verify everything passes**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: all existing tests pass, plus the new monotonicity and
`page_word_range is None` assertions.

Run the broader regression: `uv run pytest tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): wire _try_or_subdivide into detect_redactions_chunked

Replaces the inlined per-batch retry loop with a recursive helper:
on retry-exhausted failure, halve the WorkItem and recurse. The
top-level loop in detect_redactions_chunked stays the same shape —
one iteration per pages_per_batch slice — and progress callbacks
still fire only at the top-level batch boundary.

chunk_idx values now come from a globally-monotonic _IndexCounter
threaded through recursion, replacing the prior chunk_idx=batch_idx
assignment that loses uniqueness when subdivision splits a batch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: End-to-end subdivision tests

**Files:**
- Create: `tests/core/test_chunking_subdivision.py`

This is the suite that proves subdivision actually works under realistic
provider failure patterns.

- [ ] **Step 1: Create the new test file with the fixture and all nine tests**

Create `tests/core/test_chunking_subdivision.py`:

```python
"""End-to-end tests for the recursive subdivision path in
core.chunking.detect_redactions_chunked. Uses a ScriptedFailureProvider
that fails on prompts above a configurable size and succeeds otherwise,
to drive the orchestrator through real subdivision trees."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.chunking import (
    BatchError,
    MIN_CHUNK_WORDS,
    OVERLAP_WORDS,
    detect_redactions_chunked,
)
from kuroi.core.config import DEFAULT_RETRY_POLICY, RetryPolicy
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, Word
from kuroi.providers._shared import build_user_prompt


def _page(num: int, n_words: int = 1) -> Page:
    return Page(
        number=num,
        words=tuple(
            Word(idx=i, text=f"p{num}_w{i}", bbox=(float(i), 0.0, float(i + 1), 1.0))
            for i in range(n_words)
        ),
    )


def _chunk(pages: tuple[int, ...], prompt_chars: int = 100) -> ChunkRecord:
    """Build a ChunkRecord matching what real providers set.

    Real providers populate `pages` from `tuple(p.number for p in batch)`.
    The chunker's renumber step only overrides `chunk_idx` and
    `page_word_range`, so this fixture must set `pages` to the real
    page numbers for tests that assert on them.
    """
    return ChunkRecord(
        chunk_idx=0,
        pages=pages,
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=prompt_chars,
        tokens_out=prompt_chars // 4,
        duration_ms=10,
    )


class ScriptedFailureProvider:
    """Provider stub that fails iff the prompt exceeds `fail_above_chars`.

    On failure: returns ([], []) — the chunker's "subdivide" signal.
    On success: returns the configured findings (translated to absolute
    word indices via the test's setup) and a single ChunkRecord.

    Records every attempted call's prompt so tests can assert on the
    actual data the model would have seen.
    """

    name = "scripted"
    model = "scripted-1"

    def __init__(
        self,
        fail_above_chars: int,
        findings_for: Callable[[tuple[Page, ...]], list[Finding]] | None = None,
    ) -> None:
        self.fail_above_chars = fail_above_chars
        self.findings_for = findings_for or (lambda _pages: [])
        self.calls: list[tuple[tuple[Page, ...], int, str]] = []

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        prompt = build_user_prompt(pages, llm_category_ids, instructions)
        self.calls.append((pages, attempt, prompt))
        if len(prompt) > self.fail_above_chars:
            return [], []
        page_numbers = tuple(p.number for p in pages)
        return (
            self.findings_for(pages),
            [_chunk(pages=page_numbers, prompt_chars=len(prompt))],
        )


# A retry policy with no sleeps, used by every test below to keep them fast.
NO_SLEEP_POLICY = RetryPolicy(max_retries=0, backoff=0.0, backoff_multiplier=1.0)


def test_multi_page_batch_halves_on_failure() -> None:
    """4-page batch; provider fails on any 2+ page prompt. The orchestrator
    must subdivide [1,2,3,4] → [1,2]+[3,4] → [1]+[2]+[3]+[4], producing 4
    successful single-page calls and a monotonic chunk_idx sequence."""
    pages = tuple(_page(i, n_words=5) for i in range(1, 5))
    # Calibrate the threshold so single-page prompts succeed but 2+ pages
    # always exceed it.
    one_page_prompt_len = len(build_user_prompt((pages[0],), ("x",)))
    threshold = one_page_prompt_len + 10  # any 2+ page prompt is well above

    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    findings, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=4, retry_policy=NO_SLEEP_POLICY
    )

    # Exactly 4 successful single-page records, monotonic chunk_idx
    assert len(chunks) == 4
    assert [c.chunk_idx for c in chunks] == [0, 1, 2, 3]
    assert [c.pages for c in chunks] == [(1,), (2,), (3,), (4,)]
    assert findings == []


def test_single_page_halves_with_overlap_text_visible_in_both_halves() -> None:
    """1-page batch with 200 words; provider fails on any prompt longer than
    a half-with-overlap. After subdivision, the boundary words (text content
    from M-OVERLAP..M+OVERLAP in the original page) must appear in BOTH
    halves' captured prompts."""
    page = _page(7, n_words=200)
    pages = (page,)
    half_with_overlap_prompt_len = len(
        build_user_prompt((page,), ("x",))
    ) // 2 + 200  # rough generous threshold so halves succeed
    provider = ScriptedFailureProvider(fail_above_chars=half_with_overlap_prompt_len)

    detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    # Two successful sub-calls (the halves); plus one initial failed call.
    success_prompts = [
        prompt
        for (_pgs, _att, prompt) in provider.calls
        if len(prompt) <= half_with_overlap_prompt_len
    ]
    assert len(success_prompts) == 2

    # Boundary text words: original page words at indices [50..150).
    boundary_texts = {f"p7_w{i}" for i in range(100 - OVERLAP_WORDS, 100 + OVERLAP_WORDS)}
    for prompt in success_prompts:
        for text in boundary_texts:
            assert text in prompt, (
                f"boundary word {text!r} missing from one half's prompt"
            )


def test_overlap_dedupes_boundary_findings() -> None:
    """Both halves return a finding for the same overlap-zone word range;
    the final result has a single finding with the higher confidence."""
    page = _page(1, n_words=200)

    # Both halves report a finding at original-page words 95..105 — squarely
    # in the overlap zone. The provider here returns findings in the
    # *synthetic* (sliced) page's coordinates; the chunker translates back.
    def findings_for(pages: tuple[Page, ...]) -> list[Finding]:
        # Each half is a synthetic page with re-indexed words 0..N-1.
        # We invert by checking the first word's text to know which half
        # we're in (left starts at "p1_w0", right starts at "p1_w50").
        first_text = pages[0].words[0].text
        if first_text == "p1_w0":
            # Left half: original word 95 maps to local index 95
            return [Finding(page=1, start=95, end=100, kind="X", confidence="high", source="llm")]
        else:
            # Right half: original word 95 maps to local index 95 - 50 = 45
            return [Finding(page=1, start=45, end=50, kind="X", confidence="medium", source="llm")]

    threshold = len(build_user_prompt((page,), ("x",))) // 2 + 200
    provider = ScriptedFailureProvider(fail_above_chars=threshold, findings_for=findings_for)

    findings, _ = detect_redactions_chunked(
        provider, (page,), ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    assert len(findings) == 1
    assert findings[0].page == 1
    assert findings[0].start == 95
    assert findings[0].end == 100
    assert findings[0].confidence == "high"  # higher confidence wins


def test_overlapping_non_identical_findings_preserved() -> None:
    """End-to-end: two halves report findings at overlapping-but-distinct
    word ranges in the boundary zone. Dedupe keeps both — same-kind
    findings at different ranges are genuine disagreement worth surfacing
    (the redaction step naturally redacts the union)."""
    page = _page(1, n_words=200)

    # Both halves cover the boundary zone (50..150 in original-page coords).
    # We pick ranges that overlap but are not identical:
    #   Half A: original (95, 97) — local 95..97 in left half (word_range 0..150)
    #   Half B: original (96, 98) — local 46..48 in right half (word_range 50..200)
    def findings_for(pages: tuple[Page, ...]) -> list[Finding]:
        first_text = pages[0].words[0].text
        if first_text == "p1_w0":  # left half
            return [Finding(page=1, start=95, end=97, kind="X", confidence="high", source="llm")]
        return [Finding(page=1, start=46, end=48, kind="X", confidence="high", source="llm")]

    threshold = len(build_user_prompt((page,), ("x",))) // 2 + 200
    provider = ScriptedFailureProvider(fail_above_chars=threshold, findings_for=findings_for)

    findings, _ = detect_redactions_chunked(
        provider, (page,), ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    # Both kept — overlapping ranges of the same kind are different findings
    assert len(findings) == 2
    ranges = sorted((f.start, f.end) for f in findings)
    assert ranges == [(95, 97), (96, 98)]


def test_floor_raises_batch_error_with_diagnostics() -> None:
    """Single page with 80 words; provider always fails. BatchError must
    carry the page number, the failed word range, and the depth."""
    page = _page(41, n_words=80)
    provider = ScriptedFailureProvider(fail_above_chars=0)  # always fails

    with pytest.raises(BatchError) as exc_info:
        detect_redactions_chunked(
            provider, (page,), ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
        )

    err = exc_info.value
    assert err.page_numbers == (41,)
    assert err.last_failed_word_range == (0, 80)
    assert err.subdivision_levels >= 1
    msg = str(err)
    assert "page 41" in msg
    assert "80 words" in msg


def test_chunk_idx_globally_monotonic_across_subdivision() -> None:
    """Multi-page batch where pages 1-2 succeed but page 3 subdivides. The
    final chunk_idx sequence is 0..N-1 with no duplicates and no gaps."""
    pages = (
        _page(1, n_words=5),
        _page(2, n_words=5),
        _page(3, n_words=200),  # this one will subdivide
    )
    page3_full_prompt = len(build_user_prompt((pages[2],), ("x",)))
    # Threshold lets the small pages through but rejects the full page-3
    # prompt; halves of page 3 should fit.
    threshold = page3_full_prompt // 2 + 200
    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    _, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    chunk_ids = [c.chunk_idx for c in chunks]
    assert chunk_ids == list(range(len(chunks)))
    assert len(chunks) == 4  # pages 1, 2, plus 2 halves of page 3


def test_page_word_range_recorded_for_sub_page_calls() -> None:
    """Sub-page chunks have page_word_range != None in original-page
    coordinates; full-page chunks have page_word_range = None."""
    pages = (_page(1, n_words=5), _page(2, n_words=200))
    full_prompt_p2 = len(build_user_prompt((pages[1],), ("x",)))
    threshold = full_prompt_p2 // 2 + 200
    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    _, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    # First chunk is page 1 full-page; subsequent chunks are page 2 halves.
    page1_chunks = [c for c in chunks if c.pages == (1,)]
    page2_chunks = [c for c in chunks if c.pages == (2,)]
    assert all(c.page_word_range is None for c in page1_chunks)
    assert all(c.page_word_range is not None for c in page2_chunks)
    # The two page-2 halves cover the full word range with overlap
    starts = sorted(c.page_word_range[0] for c in page2_chunks)
    ends = sorted(c.page_word_range[1] for c in page2_chunks)
    assert starts[0] == 0
    assert ends[-1] == 200


def test_subdivision_runs_after_retries_not_before() -> None:
    """Retry budget is consumed first. Provider scripted to fail twice then
    succeed; with RetryPolicy(max_retries=2), the batch succeeds on attempt
    2 without subdivision. Therefore total successful chunks == 1."""
    page = _page(1, n_words=10)

    class TransientProvider:
        name = "transient"
        model = "stub-1"

        def __init__(self) -> None:
            self.attempt_count = 0
            self.calls: list[int] = []

        def detect_redactions(
            self,
            pages: tuple[Page, ...],
            llm_category_ids: tuple[str, ...],
            *,
            instructions: tuple[str, ...] = (),
            seed: int | None = None,
            attempt: int = 0,
        ) -> tuple[list[Finding], list[ChunkRecord]]:
            self.calls.append(attempt)
            if self.attempt_count < 2:
                self.attempt_count += 1
                return [], []
            return [], [_chunk(pages=tuple(p.number for p in pages))]

    provider = TransientProvider()
    policy = RetryPolicy(max_retries=2, backoff=0.0, backoff_multiplier=1.0)

    _, chunks = detect_redactions_chunked(
        provider, (page,), ("x",), pages_per_batch=1, retry_policy=policy
    )

    assert len(chunks) == 1
    assert provider.calls == [0, 1, 2]  # full retry budget consumed first


def test_progress_callbacks_fire_at_batch_boundary_only() -> None:
    """Subdivision is internal: on_batch_start / on_batch_complete fire
    once per top-level batch even when subdivision occurs underneath."""
    pages = (_page(1, n_words=5), _page(2, n_words=200))
    full_prompt_p2 = len(build_user_prompt((pages[1],), ("x",)))
    threshold = full_prompt_p2 // 2 + 200
    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    starts: list[int] = []
    completes: list[int] = []

    def on_start(idx: int, total: int, _pn: tuple[int, ...]) -> None:
        starts.append(idx)

    def on_complete(idx: int, total: int, _pn: tuple[int, ...], _ck: ChunkRecord) -> None:
        completes.append(idx)

    detect_redactions_chunked(
        provider,
        pages,
        ("x",),
        pages_per_batch=1,
        retry_policy=NO_SLEEP_POLICY,
        on_batch_start=on_start,
        on_batch_complete=on_complete,
    )

    # Two top-level batches → two starts, two completes — even though
    # batch 2 produced multiple sub-records under the hood.
    assert starts == [0, 1]
    assert completes == [0, 1]
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/core/test_chunking_subdivision.py -v`
Expected: all 9 tests pass.

If a test fails because the threshold calibration is off (the
`ScriptedFailureProvider`'s `fail_above_chars` needs to match the actual
prompt sizes the orchestrator builds), adjust the threshold heuristic
in that specific test until it cleanly distinguishes "should fail" from
"should succeed". The test's intent is what matters — calibration
slack is fine.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/core/test_chunking_subdivision.py
git commit -m "$(cat <<'EOF'
test(chunking): end-to-end subdivision suite

Nine tests driving the full subdivision pipeline through a
ScriptedFailureProvider that rejects oversized prompts. Covers:
multi-page halving, single-page halving with overlap, dedupe of
boundary findings, preservation of overlapping-but-non-identical
findings, floor BatchError diagnostics, monotonic chunk_idx,
page_word_range recording, post-retry subdivision ordering, and
progress-callback firing at the top-level batch boundary only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: CLI BatchError formatting

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Test: `tests/cli/test_run.py` (existing test at line 979)

The current CLI block (lines 264–270) renders the legacy single-line
message. The new floor-mode `BatchError` already includes a
multi-line diagnostic in `str(exc)`, but the CLI's "Re-run with a
smaller --pages-per-batch" hint is misleading after subdivision lands —
the pages-per-batch flag won't help if a single page is the problem.

- [ ] **Step 1: Write a failing test for the new CLI message**

Inspect `tests/cli/test_run.py` around line 979 (the existing
`...exits 1 with a BatchError message` test). The test injects a
hard-failing provider; with subdivision, it'll subdivide all the way
to the floor (single-page tests use 1-word pages, so the floor is hit
immediately) and emit the rich diagnostic.

Append a new test below the existing one:

```python
def test_run_floor_batch_error_renders_multi_line_diagnostic(
    cli_runner: CliRunner,
    pdf_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When BatchError carries floor diagnostics, the CLI prints the full
    multi-line message rather than the legacy one-liner-plus-hint."""
    # Arrange: a hard-failing provider on a single tiny page → floor
    # BatchError after one subdivision level.
    from kuroi.core.chunking import BatchError

    pdf = pdf_factory(["Hello World"])
    output = tmp_path / "out.pdf"

    def _fake_provider(*_args: object, **_kwargs: object) -> object:
        class P:
            name = "stub"
            model = "stub-1"

            def detect_redactions(
                self, *_a: object, **_kw: object
            ) -> tuple[list, list]:
                return [], []

        return P()

    monkeypatch.setattr("kuroi.cli.run.make_provider", _fake_provider)

    # Act
    result = cli_runner.invoke(
        app, ["run", str(pdf), "-o", str(output), "--yes", "--max-retries", "0"]
    )

    # Assert: rich diagnostic, not the old one-liner hint
    assert result.exit_code == 1
    out = result.stdout
    assert "could not be processed" in out
    assert "Likely causes" in out
    assert "Suggestions" in out
    # The misleading old hint must be absent for floor failures
    assert "Re-run with a smaller --pages-per-batch" not in out
```

(If the test file's existing fixtures use different names than
`pdf_factory` / `cli_runner` / `app`, adapt to match — see how the
sibling test at line 979 wires its provider and PDF.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/cli/test_run.py::test_run_floor_batch_error_renders_multi_line_diagnostic -v`
Expected: failure — the existing CLI block always appends the
"Re-run with a smaller --pages-per-batch" hint.

- [ ] **Step 3: Update the CLI BatchError handler**

In `src/kuroi/cli/run.py`, replace the `except BatchError as exc:` block
(currently around lines 264–270):

```python
            except BatchError as exc:
                console.print(
                    f"  [red]{exc}[/]\n"
                    f"  Re-run with a smaller --pages-per-batch, "
                    f"or check the WARNING(s) above for the cause."
                )
                raise typer.Exit(code=1) from exc
```

with:

```python
            except BatchError as exc:
                if exc.subdivision_levels > 0:
                    # Floor mode: the message itself is multi-line and
                    # already includes targeted suggestions.
                    console.print(f"  [red]{exc}[/]")
                else:
                    # Legacy multi-page failure: prepend a short hint so
                    # the user knows their levers (smaller batch, longer
                    # retries).
                    console.print(
                        f"  [red]{exc}[/]\n"
                        f"  Re-run with a smaller --pages-per-batch, "
                        f"or check the WARNING(s) above for the cause."
                    )
                raise typer.Exit(code=1) from exc
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/cli/test_run.py -v`
Expected: all pass — the new floor test plus the existing BatchError
test (whose provider triggers a non-floor error path on a multi-word
page).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "$(cat <<'EOF'
feat(cli): render floor BatchError diagnostics multi-line

When BatchError carries subdivision_levels > 0, print the rich
multi-line diagnostic that BatchError builds — it already includes
likely-causes and tailored suggestions. Suppress the legacy
'Re-run with a smaller --pages-per-batch' hint in that case
(misleading: subdivision already tried and the pages-per-batch knob
doesn't help below the page level).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Documentation updates

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/user-guide/troubleshooting.md` (create section if missing)
- Modify: `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`

- [ ] **Step 1: Update `CHANGELOG.md`**

Open `CHANGELOG.md`. Under the existing `## [Unreleased]` section, add
to `### Added` (creating the subsection if it isn't already present):

```markdown
- Automatic subdivision on batch failure. When a batch still fails
  after retries, kuroi now halves it (by pages, then by word range
  with a 50-word overlap) and recurses, instead of aborting the run.
  Single pages too dense for the active model continue to surface a
  clear `BatchError` with diagnostics. New optional `page_word_range`
  field on the `chunk_request` audit record records sub-page slices.
```

Add to `### Fixed`:

```markdown
- Anthropic responses truncated at `max_tokens` previously emitted a
  chunk record with zero findings — silent data loss. They now signal
  the chunker to subdivide, recovering the lost findings on smaller
  prompts.
- Anthropic `prompt is too long` errors no longer crash the run; they
  are caught and translated into a subdivide signal.
- Ollama malformed-content responses (missing `message.content`,
  non-JSON body despite `format: "json"`, non-object payload)
  previously emitted a chunk record with zero findings. They now
  trigger subdivision instead.
```

(If a `### Fixed` subsection doesn't exist yet under `[Unreleased]`,
add it.)

- [ ] **Step 2: Update troubleshooting**

Check `docs/user-guide/troubleshooting.md`. If it exists, append:

```markdown
## "Could not be processed even after subdividing"

Hitting a `BatchError: page N (M words) could not be processed even
after subdividing to the minimum chunk size`? The active model can't
handle even a small slice of that page.

- Try a model with a larger context window (Anthropic Sonnet/Opus,
  larger Ollama models).
- For Ollama, raise `--max-retries` to give a slow deployment more
  per-attempt wall clock — the timeout grows linearly with attempt
  number (120s × (attempt + 1)), so `--max-retries 5` extends the
  per-attempt budget to 720s.
```

If `docs/user-guide/troubleshooting.md` does not exist yet, create it
with that content as the only section (keep it minimal — it'll grow
organically).

- [ ] **Step 3: Update the audit-schema reference**

Open `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`
and find the `chunk_request` schema section (it's referenced from
`core/audit_records.py:5`). Add a row for the new optional field:

```markdown
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| ... | ... | ... | ... |
| `page_word_range` | `[int, int]` or null | No | Original-page `(word_start, word_end)` for sub-page calls (when subdivision split a single page). `null` for full-page calls. Added in 0.2 (subdivide-on-failure). |
```

Match the surrounding schema-table style; if the existing table uses a
different format, adapt the row to fit.

- [ ] **Step 4: Sanity-check docs render**

Run: `uv run mkdocs build --strict 2>&1 | head -20` (only if the docs
build is wired locally — see `Makefile` target). Otherwise just confirm
the markdown renders by opening the files.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/user-guide/troubleshooting.md docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md
git commit -m "$(cat <<'EOF'
docs: changelog + troubleshooting + audit schema for subdivide-on-failure

Documents the new subdivision behavior, the three silent-data-loss
fixes (Anthropic truncation, Anthropic prompt-too-long, Ollama
malformed content), the floor BatchError, and the new optional
page_word_range field on the chunk_request audit record.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Checklist

After all tasks land, run these sanity checks:

- [ ] `uv run pytest tests/ -v` — every test passes.
- [ ] `uv run mypy src/` — no new type errors introduced.
- [ ] `uv run ruff check src/ tests/` — clean.
- [ ] Manually run an end-to-end smoke: `kuroi run` against a small PDF
      with the default Anthropic provider — verify nothing regressed
      on the happy path.
- [ ] `git log --oneline` since the start of this plan: roughly 15
      commits, each one a focused atomic change.
