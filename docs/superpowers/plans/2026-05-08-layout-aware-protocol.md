# Layout-Aware Word-Index Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap kuroi's LLM prompt with PyMuPDF block boundaries (`<block id="N">[idx]token...</block>`) behind an opt-in `--layout-aware` flag, so the model sees paragraph and other layout boundaries while the response schema and bbox round-trip stay unchanged.

**Architecture:** `Word` gains a `block_id` field populated from PyMuPDF's existing `get_text("words")` tuple (no new extraction pass, no new dependencies). `serialize_for_llm` gains a `layout_aware` keyword that wraps consecutive same-block words in `<block>` tags. `providers/_shared.py` grows a `build_system_prompt(layout_aware)` helper and forwards `layout_aware` through `build_user_prompt`. The flag threads from CLI → `Config` → `detect_redactions_chunked` → both providers. Default is off; existing runs are byte-identical.

**Tech Stack:** Python 3.12, dataclasses, PyMuPDF, pytest, mypy, ruff, uv.

---

## Spec Reference

Implements `docs/superpowers/specs/2026-05-08-layout-aware-protocol-design.md`.

---

## File Structure

**Modify (source):**
- `src/kuroi/core/pdf.py` — add `block_id: int = 0` to `Word`; populate it in `extract_word_index` and the OCR path; add `layout_aware: bool = False` keyword to `serialize_for_llm`; add private `_serialize_blocks` helper.
- `src/kuroi/providers/_shared.py` — add `LAYOUT_AWARE_INSTRUCTIONS` constant and `build_system_prompt(layout_aware: bool) -> str`; add `layout_aware: bool = False` keyword to `build_user_prompt` and forward to `serialize_for_llm`.
- `src/kuroi/providers/base.py` — add `layout_aware: bool = False` keyword to the `Provider.detect_redactions` Protocol method.
- `src/kuroi/providers/anthropic.py` — accept `layout_aware`; replace literal `system=SYSTEM_PROMPT` with `system=build_system_prompt(layout_aware)`; pass `layout_aware` to `build_user_prompt`.
- `src/kuroi/providers/ollama.py` — symmetric change to Anthropic.
- `src/kuroi/core/chunking.py` — add `layout_aware: bool = False` keyword to `detect_redactions_chunked`; forward to the single `provider.detect_redactions(...)` call.
- `src/kuroi/core/config.py` — add `layout_aware: bool = False` to `Config` and `ConfigOverrides`; add `_read_prompt_config` helper; integrate into `resolve_config`.
- `src/kuroi/cli/run.py` — add `--layout-aware` / `--no-layout-aware` typer flag; pass through `ConfigOverrides`; thread `config.layout_aware` to `detect_redactions_chunked`; conditionally append `blocks=N` to the per-batch progress line.

**Modify (tests):**
- `tests/core/test_pdf.py` — `block_id` extraction tests, default-path regression lock, layout-aware serialization tests.
- `tests/providers/test_anthropic.py` — system-prompt and user-prompt propagation tests.
- `tests/providers/test_ollama.py` — symmetric to Anthropic.
- `tests/core/test_chunking.py` — orchestrator forwards `layout_aware`.
- `tests/core/test_config.py` — `[prompt] layout_aware` resolution from file, override, default.
- `tests/cli/test_run.py` — CLI flag wiring.

**Create (tests):**
- `tests/providers/test_shared.py` — new file for `build_system_prompt` (unit tests on the shared helper itself).

**Modify (docs):**
- `CHANGELOG.md` — entry under `[Unreleased]`.
- `docs/reference/config.md` — `[prompt]` section.
- `zensical.toml` — register the new prompt-tuning page in the user-guide nav.
- `docs/user-guide/prompt-tuning.md` — new short page (when to enable, expected overhead, current limits).

---

## Tasks

### Task 1: `Word` gains `block_id`

The default value of `0` keeps the existing fixtures (which construct
`Word` without `block_id`) working unchanged.

**Files:**
- Modify: `src/kuroi/core/pdf.py:18-22`
- Test: `tests/core/test_pdf.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_pdf.py`:

```python
def test_word_has_block_id_default_zero() -> None:
    from kuroi.core.pdf import Word

    w = Word(idx=0, text="hello", bbox=(0.0, 0.0, 1.0, 1.0))

    assert w.block_id == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pdf.py::test_word_has_block_id_default_zero -v`
Expected: FAIL with `AttributeError: 'Word' object has no attribute 'block_id'`

- [ ] **Step 3: Add the field**

In `src/kuroi/core/pdf.py`, replace the `Word` dataclass:

```python
@dataclass(frozen=True)
class Word:
    idx: int
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points
    block_id: int = 0  # PyMuPDF block_no; default 0 for synthetic/OCR pages
```

- [ ] **Step 4: Run test to verify it passes, plus type-check and full suite**

Run:
- `uv run pytest tests/core/test_pdf.py::test_word_has_block_id_default_zero -v`
- `make typecheck`
- `make test`

Expected: pytest target PASS; mypy clean; full suite green (existing fixtures unaffected because the new field defaults).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py
git commit -m "feat(pdf): add block_id field to Word"
```

---

### Task 2: `extract_word_index` populates `block_id` from PyMuPDF

PyMuPDF's `get_text("words")` returns 8-tuples shaped
`(x0, y0, x1, y1, text, block_no, line_no, word_no)`. Today the
extractor reads `w[0..4]`; this task adds `w[5]`.

**Files:**
- Modify: `src/kuroi/core/pdf.py:69-77` (the comprehension inside `extract_word_index`)
- Test: `tests/core/test_pdf.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_pdf.py`:

```python
def test_extract_word_index_populates_block_id(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["First paragraph here.\n\nSecond paragraph here."])

    result = extract_word_index(pdf)
    page = result.pages[0]

    block_ids = {w.block_id for w in page.words}
    # Two visually-separated paragraphs produce at least two distinct blocks.
    assert len(block_ids) >= 2
    # Words sharing a paragraph share a block_id.
    first_three = page.words[:3]
    assert len({w.block_id for w in first_three}) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pdf.py::test_extract_word_index_populates_block_id -v`
Expected: FAIL — all words have `block_id == 0` (the default), so `len(block_ids) == 1`.

- [ ] **Step 3: Update the extractor**

In `src/kuroi/core/pdf.py`, inside `extract_word_index`, the comprehension at lines ~69-77:

```python
words = tuple(
    Word(
        idx=i,
        text=str(w[4]),
        bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
        block_id=int(w[5]),
    )
    for i, w in enumerate(raw)
)
```

- [ ] **Step 4: Run test to verify it passes, plus full suite**

Run:
- `uv run pytest tests/core/test_pdf.py::test_extract_word_index_populates_block_id -v`
- `make test`

Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py
git commit -m "feat(pdf): populate Word.block_id from PyMuPDF in extract_word_index"
```

---

### Task 3: OCR path also populates `block_id`

The OCR path (`_ocr_page_words` → `pages[page_1idx - 1] = Page(...)` at
`src/kuroi/core/pdf.py:84-95`) currently has its own comprehension that
ignores `w[5]`. Make it symmetric with the native path.

**Files:**
- Modify: `src/kuroi/core/pdf.py:84-95` (OCR comprehension)
- Test: `tests/core/test_pdf.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_pdf.py`:

```python
def test_ocr_path_populates_block_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCR'd words inherit whatever block_id PyMuPDF assigns; the test
    asserts that the *field is populated* from the tuple, not that the
    value is any specific number."""
    from kuroi.core import pdf as pdf_module

    captured: dict[str, list[tuple[float, float, float, float, str, int, int, int]]] = {}

    class FakePage:
        def __init__(self) -> None:
            self.number = 1

        def get_text(self, mode: str, **kwargs: object) -> list[tuple[Any, ...]]:
            # Native pass returns words with block_id=0; OCR pass overrides with block_id=42.
            if "textpage" in kwargs:
                tup = (0.0, 0.0, 1.0, 1.0, "ocr", 42, 0, 0)
                captured["ocr_words"] = [tup]
                return [tup]
            return [(0.0, 0.0, 1.0, 1.0, "native", 0, 0, 0)]

        def get_images(self) -> list[object]:
            return [object()]  # trigger the OCR branch

        def get_textpage_ocr(self, **kwargs: object) -> object:
            return object()

    class FakeDoc:
        page_count = 1

        def __getitem__(self, _: int) -> FakePage:
            return FakePage()

        def close(self) -> None:
            pass

    monkeypatch.setattr(pdf_module.pymupdf, "open", lambda _: FakeDoc())
    monkeypatch.setattr(pdf_module.shutil, "which", lambda _: "/usr/bin/tesseract")

    result = pdf_module.extract_word_index(Path("ignored.pdf"))

    assert result.ocr_page_count == 1
    page = result.pages[0]
    assert len(page.words) == 1
    assert page.words[0].block_id == 42
    assert page.words[0].text == "ocr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pdf.py::test_ocr_path_populates_block_id -v`
Expected: FAIL — OCR comprehension ignores `w[5]`, so `block_id == 0`.

- [ ] **Step 3: Update the OCR comprehension**

In `src/kuroi/core/pdf.py`, the OCR replacement block at lines ~84-95:

```python
for page_1idx in scan_candidates:
    pdf_page = doc[page_1idx - 1]
    raw = _ocr_page_words(pdf_page)
    words = tuple(
        Word(
            idx=i,
            text=str(w[4]),
            bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
            block_id=int(w[5]),
        )
        for i, w in enumerate(raw)
    )
    pages[page_1idx - 1] = Page(number=page_1idx, words=words)
```

- [ ] **Step 4: Run test to verify it passes, plus full suite**

Run:
- `uv run pytest tests/core/test_pdf.py::test_ocr_path_populates_block_id -v`
- `make test`

Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py
git commit -m "feat(pdf): populate Word.block_id from OCR path"
```

---

### Task 4: Lock the default `serialize_for_llm` shape

Before adding the `layout_aware` keyword, write a regression test that
locks the no-flag output byte-for-byte. This test prevents accidental
behavior change to existing runs.

**Files:**
- Test: `tests/core/test_pdf.py` (append)

- [ ] **Step 1: Write the regression-lock test**

Append to `tests/core/test_pdf.py`:

```python
def test_serialize_for_llm_default_no_block_tags() -> None:
    from kuroi.core.pdf import Page, Word, serialize_for_llm

    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Hello", bbox=(0, 0, 1, 1), block_id=3),
                Word(idx=1, text="world", bbox=(1, 0, 2, 1), block_id=3),
                Word(idx=2, text="Other", bbox=(0, 1, 1, 2), block_id=4),
            ),
        ),
    )

    text = serialize_for_llm(pages)

    # Default path: no <block> markers anywhere.
    assert "<block" not in text
    assert text == '<page n="1">\n[0]Hello [1]world [2]Other\n</page>'
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/core/test_pdf.py::test_serialize_for_llm_default_no_block_tags -v`
Expected: PASS (no implementation change yet — this is just locking what's already true).

- [ ] **Step 3: Commit**

```bash
git add tests/core/test_pdf.py
git commit -m "test(pdf): regression-lock default serialize_for_llm output"
```

---

### Task 5: `serialize_for_llm` gains `layout_aware`

Add the keyword and the `_serialize_blocks` helper. The default
behavior is unchanged (locked by Task 4); the new branch wraps
consecutive same-`block_id` words in `<block id="N">…</block>`.

**Files:**
- Modify: `src/kuroi/core/pdf.py:102-117`
- Test: `tests/core/test_pdf.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_pdf.py`:

```python
def _layout_pages() -> tuple[object, ...]:
    from kuroi.core.pdf import Page, Word

    return (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Heading", bbox=(0, 0, 1, 1), block_id=7),
                Word(idx=1, text="First", bbox=(0, 1, 1, 2), block_id=8),
                Word(idx=2, text="paragraph", bbox=(1, 1, 2, 2), block_id=8),
                Word(idx=3, text="Footer", bbox=(0, 9, 1, 10), block_id=12),
            ),
        ),
    )


def test_serialize_for_llm_layout_aware_wraps_blocks() -> None:
    from kuroi.core.pdf import serialize_for_llm

    text = serialize_for_llm(_layout_pages(), layout_aware=True)

    # Three blocks emitted in document order; idx markers preserved inside.
    assert '<block id="7">[0]Heading</block>' in text
    assert '<block id="8">[1]First [2]paragraph</block>' in text
    assert '<block id="12">[3]Footer</block>' in text
    assert text.startswith('<page n="1">')
    assert text.endswith("</page>")


def test_serialize_for_llm_layout_aware_collapses_consecutive_same_block() -> None:
    from kuroi.core.pdf import Page, Word, serialize_for_llm

    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="a", bbox=(0, 0, 1, 1), block_id=5),
                Word(idx=1, text="b", bbox=(1, 0, 2, 1), block_id=5),
                Word(idx=2, text="c", bbox=(2, 0, 3, 1), block_id=5),
            ),
        ),
    )

    text = serialize_for_llm(pages, layout_aware=True)

    # One block tag wraps all three words; no spurious second tag.
    assert text.count('<block id="5">') == 1
    assert '<block id="5">[0]a [1]b [2]c</block>' in text


def test_serialize_for_llm_layout_aware_split_block_across_pages() -> None:
    """A block_id that appears on two pages emits twice — once per page —
    because PyMuPDF block numbering is per-page."""
    from kuroi.core.pdf import Page, Word, serialize_for_llm

    pages = (
        Page(
            number=1,
            words=(Word(idx=0, text="a", bbox=(0, 0, 1, 1), block_id=2),),
        ),
        Page(
            number=2,
            words=(Word(idx=0, text="b", bbox=(0, 0, 1, 1), block_id=2),),
        ),
    )

    text = serialize_for_llm(pages, layout_aware=True)

    assert text.count('<block id="2">') == 2
    assert '<page n="1">\n<block id="2">[0]a</block>\n</page>' in text
    assert '<page n="2">\n<block id="2">[0]b</block>\n</page>' in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_pdf.py -k "layout_aware" -v`
Expected: FAIL — `serialize_for_llm()` does not accept `layout_aware`.

- [ ] **Step 3: Implement the helper and the keyword**

Replace `serialize_for_llm` in `src/kuroi/core/pdf.py` (and add the helper above it):

```python
def _serialize_blocks(words: tuple[Word, ...]) -> str:
    out: list[str] = []
    current_block_id: int | None = None
    current_words: list[Word] = []

    def flush() -> None:
        if current_words:
            inner = " ".join(f"[{w.idx}]{w.text}" for w in current_words)
            out.append(f'<block id="{current_block_id}">{inner}</block>')

    for w in words:
        if w.block_id != current_block_id:
            flush()
            current_words = [w]
            current_block_id = w.block_id
        else:
            current_words.append(w)
    flush()
    return "\n".join(out)


def serialize_for_llm(
    pages: tuple[Page, ...],
    *,
    layout_aware: bool = False,
) -> str:
    """Render the word index as the prompt-side representation.

    Default (layout_aware=False) output:
        <page n="1">
        [0]Hello [1]world
        </page>

    With layout_aware=True, each page's content is wrapped in
    <block id="N">…</block> sections corresponding to the PyMuPDF
    block_id assignments on each Word.
    """
    chunks: list[str] = []
    for page in pages:
        if layout_aware:
            body = _serialize_blocks(page.words)
        else:
            body = " ".join(f"[{w.idx}]{w.text}" for w in page.words)
        chunks.append(f'<page n="{page.number}">\n{body}\n</page>')
    return "\n".join(chunks)
```

- [ ] **Step 4: Run all `serialize_for_llm` tests, including the regression-lock**

Run: `uv run pytest tests/core/test_pdf.py -v`
Expected: All `serialize_for_llm` tests PASS, including the Task 4 regression lock.

- [ ] **Step 5: Type-check and full suite**

Run:
- `make typecheck`
- `make test`

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py
git commit -m "feat(pdf): add layout_aware option to serialize_for_llm"
```

---

### Task 6: `build_system_prompt` helper in `_shared.py`

Add the helper that returns the existing system prompt unchanged when
off, and the prompt + layout-aware paragraph when on. Keep
`SYSTEM_PROMPT` as the *base* constant (the layout-aware version is
constructed by the helper, not stored as a second constant).

**Files:**
- Modify: `src/kuroi/providers/_shared.py:13-28`
- Test: `tests/providers/test_shared.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `tests/providers/test_shared.py`:

```python
"""Unit tests for shared provider helpers."""

from __future__ import annotations

from kuroi.providers._shared import (
    SYSTEM_PROMPT,
    build_system_prompt,
)


def test_build_system_prompt_off_returns_base() -> None:
    assert build_system_prompt(layout_aware=False) == SYSTEM_PROMPT


def test_build_system_prompt_on_appends_block_explanation() -> None:
    prompt = build_system_prompt(layout_aware=True)

    assert prompt.startswith(SYSTEM_PROMPT)
    assert "<block id=" in prompt
    assert "use the per-page word indices" in prompt.lower()
    assert "do not report block ids" in prompt.lower()


def test_build_system_prompt_on_is_strictly_longer_than_off() -> None:
    on = build_system_prompt(layout_aware=True)
    off = build_system_prompt(layout_aware=False)
    assert len(on) > len(off)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_shared.py -v`
Expected: FAIL — `build_system_prompt` does not exist.

- [ ] **Step 3: Add the constant and helper**

Insert into `src/kuroi/providers/_shared.py` immediately after the
`SYSTEM_PROMPT` constant (around line 28):

```python
LAYOUT_AWARE_INSTRUCTIONS = (
    "\n\n"
    "The page content is organized into <block id=\"N\">...</block> sections "
    "corresponding to layout-detected paragraphs and other typographic units. "
    "Use the block boundaries as context to disambiguate which words refer to "
    "the same entity (e.g. a field label vs its value), but report findings "
    "exactly as before, using the per-page word indices [N], not block IDs. "
    "Do not report block IDs in your response."
)


def build_system_prompt(layout_aware: bool) -> str:
    """Return the system prompt for the model, with optional layout-aware addendum."""
    if layout_aware:
        return SYSTEM_PROMPT + LAYOUT_AWARE_INSTRUCTIONS
    return SYSTEM_PROMPT
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/providers/test_shared.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/_shared.py tests/providers/test_shared.py
git commit -m "feat(providers): add build_system_prompt helper for layout-aware mode"
```

---

### Task 7: `build_user_prompt` accepts `layout_aware`

Forward the keyword to `serialize_for_llm` so the user message contains
`<block>` tags when on.

**Files:**
- Modify: `src/kuroi/providers/_shared.py:36-50`
- Test: `tests/providers/test_shared.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_shared.py`:

```python
from kuroi.core.pdf import Page, Word
from kuroi.providers._shared import build_user_prompt


def _page() -> Page:
    return Page(
        number=1,
        words=(
            Word(idx=0, text="A", bbox=(0, 0, 1, 1), block_id=3),
            Word(idx=1, text="B", bbox=(1, 0, 2, 1), block_id=4),
        ),
    )


def test_build_user_prompt_default_no_block_tags() -> None:
    prompt = build_user_prompt(
        (_page(),),
        llm_category_ids=("kind",),
    )

    assert "<block" not in prompt
    assert "[0]A [1]B" in prompt


def test_build_user_prompt_layout_aware_includes_block_tags() -> None:
    prompt = build_user_prompt(
        (_page(),),
        llm_category_ids=("kind",),
        layout_aware=True,
    )

    assert '<block id="3">[0]A</block>' in prompt
    assert '<block id="4">[1]B</block>' in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_shared.py -k "user_prompt" -v`
Expected: FAIL — `build_user_prompt` does not accept `layout_aware`.

- [ ] **Step 3: Update the signature**

In `src/kuroi/providers/_shared.py`, replace `build_user_prompt`:

```python
def build_user_prompt(
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    instructions: tuple[str, ...] = (),
    *,
    layout_aware: bool = False,
) -> str:
    """Construct the user-message body sent to the model."""
    doc = serialize_for_llm(pages, layout_aware=layout_aware)
    parts: list[str] = []
    if llm_category_ids:
        cats = ", ".join(llm_category_ids)
        parts.append(f"Active LLM categories: {cats}\n\n")
    if instructions:
        instr = "; ".join(instructions)
        parts.append(f"Redaction instructions: {instr}\n\n")
    return "".join(parts) + f"Output schema: {OUTPUT_SCHEMA_HINT}\n\n<document>\n{doc}\n</document>"
```

Note: `instructions` stays positional-or-keyword (existing callers pass
it positionally); `layout_aware` is keyword-only to make the boolean
explicit at call sites.

- [ ] **Step 4: Run tests to verify they pass + full suite**

Run:
- `uv run pytest tests/providers/test_shared.py -v`
- `make test`

Expected: PASS; existing callers (Anthropic, Ollama) continue to work because they pass three positional args and don't yet pass `layout_aware`.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/_shared.py tests/providers/test_shared.py
git commit -m "feat(providers): add layout_aware keyword to build_user_prompt"
```

---

### Task 8: Provider Protocol gains `layout_aware`

Add the keyword to the abstract `Provider.detect_redactions` signature
in `base.py` so concrete providers can accept it without violating the
Protocol.

**Files:**
- Modify: `src/kuroi/providers/base.py:18-43`

- [ ] **Step 1: Update the Protocol**

In `src/kuroi/providers/base.py`, replace the `detect_redactions`
signature:

```python
def detect_redactions(
    self,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    attempt: int = 0,
    layout_aware: bool = False,
) -> tuple[list[Finding], list[ChunkRecord]]:
    """Identify spans to redact across the supplied pages.

    Args:
        pages: Word-indexed pages produced by the PDF extractor.
        llm_category_ids: Category ids the provider is responsible for.
        instructions: Free-text redaction instructions from the user.
        seed: Optional sampling seed for reproducible runs.
        attempt: Zero-based retry index from the chunker. Providers may use
            it to scale per-call timeouts (e.g. Ollama gives slow models
            more time on each retry).
        layout_aware: When True, wrap the prompt with PyMuPDF block
            boundaries (<block id="N">…</block>) so the model sees
            paragraph and other layout-detected structure. Default False.

    Returns:
        A tuple of `(findings, chunk_records)` — findings are the proposed
        redactions, chunk_records audit the prompts and raw responses for
        each chunk dispatched to the model.
    """
    ...
```

- [ ] **Step 2: Type-check and run full suite**

Run:
- `make typecheck`
- `make test`

Expected: clean. (No tests assert against the Protocol directly; this just widens the contract.)

- [ ] **Step 3: Commit**

```bash
git add src/kuroi/providers/base.py
git commit -m "feat(providers): add layout_aware to Provider.detect_redactions Protocol"
```

---

### Task 9: `AnthropicProvider` accepts and forwards `layout_aware`

Thread the keyword through to `build_system_prompt` and
`build_user_prompt`.

**Files:**
- Modify: `src/kuroi/providers/anthropic.py:19-23, 64-98`
- Test: `tests/providers/test_anthropic.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_anthropic.py`:

```python
def test_anthropic_layout_aware_off_uses_plain_system_prompt() -> None:
    from kuroi.providers._shared import SYSTEM_PROMPT, LAYOUT_AWARE_INSTRUCTIONS

    pages = (_page(1, ["Hello", "world"]),)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"findings": []}')],
        usage=MagicMock(input_tokens=10, output_tokens=2),
        system_fingerprint=None,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=fake_client)

    provider.detect_redactions(pages, llm_category_ids=("k",))

    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["system"] == SYSTEM_PROMPT
    assert LAYOUT_AWARE_INSTRUCTIONS not in kwargs["system"]
    assert "<block" not in kwargs["messages"][0]["content"]


def test_anthropic_layout_aware_on_appends_paragraph_and_wraps_user_prompt() -> None:
    from kuroi.providers._shared import LAYOUT_AWARE_INSTRUCTIONS

    pages = (_page(1, ["Hello", "world"]),)
    # Give both words distinct block_ids so layout-aware emits two block tags.
    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Hello", bbox=(0, 0, 1, 1), block_id=1),
                Word(idx=1, text="world", bbox=(1, 0, 2, 1), block_id=2),
            ),
        ),
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"findings": []}')],
        usage=MagicMock(input_tokens=10, output_tokens=2),
        system_fingerprint=None,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=fake_client)

    provider.detect_redactions(pages, llm_category_ids=("k",), layout_aware=True)

    kwargs = fake_client.messages.create.call_args.kwargs
    assert LAYOUT_AWARE_INSTRUCTIONS in kwargs["system"]
    user_content = kwargs["messages"][0]["content"]
    assert '<block id="1">[0]Hello</block>' in user_content
    assert '<block id="2">[1]world</block>' in user_content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_anthropic.py -k "layout_aware" -v`
Expected: FAIL — `detect_redactions` doesn't accept `layout_aware`, and `system=` is hardcoded to `SYSTEM_PROMPT`.

- [ ] **Step 3: Update the provider**

In `src/kuroi/providers/anthropic.py`, update the import:

```python
from kuroi.providers._shared import (
    build_system_prompt,
    build_user_prompt,
    parse_findings_payload,
)
```

(Drop `SYSTEM_PROMPT` from the import — `build_system_prompt` is the new entry point.)

Then update `detect_redactions`:

```python
def detect_redactions(
    self,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    attempt: int = 0,
    layout_aware: bool = False,
) -> tuple[list[Finding], list[ChunkRecord]]:
    del attempt  # accepted for Provider protocol compliance; Anthropic SDK has its own retry/timeout
    if not llm_category_ids and not instructions:
        return [], []
    user_prompt = build_user_prompt(
        pages, llm_category_ids, instructions, layout_aware=layout_aware
    )
    prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()

    extra: dict[str, Any] = {} if self.model in _NO_TEMPERATURE_MODELS else {"temperature": 0}

    logger.debug(
        "anthropic request model=%s max_tokens=%d prompt_chars=%d prompt_sha=%s\n"
        "FULL PROMPT:\n%s",
        self.model,
        self._max_tokens,
        len(user_prompt),
        prompt_sha[:8],
        user_prompt,
    )

    started = time.monotonic()
    response = self._client.messages.create(
        model=self.model,
        max_tokens=self._max_tokens,
        system=build_system_prompt(layout_aware),
        messages=[{"role": "user", "content": user_prompt}],
        **extra,
    )
    # ... rest of method unchanged
```

(Only the import block, `detect_redactions` signature, the `build_user_prompt` call, and the `system=` argument change. Everything else stays.)

- [ ] **Step 4: Run new and existing Anthropic tests**

Run:
- `uv run pytest tests/providers/test_anthropic.py -v`
- `make typecheck`

Expected: PASS; existing tests unaffected because their default invocations don't pass `layout_aware` (so the prompt is byte-identical).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/anthropic.py tests/providers/test_anthropic.py
git commit -m "feat(anthropic): forward layout_aware through detect_redactions"
```

---

### Task 10: `OllamaProvider` accepts and forwards `layout_aware`

Symmetric to Task 9 for Ollama.

**Files:**
- Modify: `src/kuroi/providers/ollama.py:16-20, 61-87`
- Test: `tests/providers/test_ollama.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_ollama.py`:

```python
def test_ollama_layout_aware_off_uses_plain_system_prompt() -> None:
    from kuroi.providers._shared import SYSTEM_PROMPT, LAYOUT_AWARE_INSTRUCTIONS

    pages = (_page(1, ["Hello", "world"]),)
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "message": {"content": '{"findings": []}'},
        "prompt_eval_count": 10,
        "eval_count": 2,
    }
    fake_client.post.return_value = fake_response
    provider = OllamaProvider(model="qwen3", url="http://x", client=fake_client)

    provider.detect_redactions(pages, llm_category_ids=("k",))

    body = fake_client.post.call_args.kwargs["json"]
    sys_msg = next(m for m in body["messages"] if m["role"] == "system")
    user_msg = next(m for m in body["messages"] if m["role"] == "user")
    assert sys_msg["content"] == SYSTEM_PROMPT
    assert LAYOUT_AWARE_INSTRUCTIONS not in sys_msg["content"]
    assert "<block" not in user_msg["content"]


def test_ollama_layout_aware_on_appends_paragraph_and_wraps_user_prompt() -> None:
    from kuroi.providers._shared import LAYOUT_AWARE_INSTRUCTIONS

    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Hello", bbox=(0, 0, 1, 1), block_id=1),
                Word(idx=1, text="world", bbox=(1, 0, 2, 1), block_id=2),
            ),
        ),
    )
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "message": {"content": '{"findings": []}'},
        "prompt_eval_count": 10,
        "eval_count": 2,
    }
    fake_client.post.return_value = fake_response
    provider = OllamaProvider(model="qwen3", url="http://x", client=fake_client)

    provider.detect_redactions(pages, llm_category_ids=("k",), layout_aware=True)

    body = fake_client.post.call_args.kwargs["json"]
    sys_msg = next(m for m in body["messages"] if m["role"] == "system")
    user_msg = next(m for m in body["messages"] if m["role"] == "user")
    assert LAYOUT_AWARE_INSTRUCTIONS in sys_msg["content"]
    assert '<block id="1">[0]Hello</block>' in user_msg["content"]
    assert '<block id="2">[1]world</block>' in user_msg["content"]
```

(Reuse the existing `_page` helper at the top of `test_ollama.py`. If it
isn't there, copy the one from `test_anthropic.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/providers/test_ollama.py -k "layout_aware" -v`
Expected: FAIL.

- [ ] **Step 3: Update the provider**

In `src/kuroi/providers/ollama.py`, update the import:

```python
from kuroi.providers._shared import (
    build_system_prompt,
    build_user_prompt,
    parse_findings_payload,
)
```

(Drop `SYSTEM_PROMPT`.)

Then update `detect_redactions`:

```python
def detect_redactions(
    self,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    attempt: int = 0,
    layout_aware: bool = False,
) -> tuple[list[Finding], list[ChunkRecord]]:
    if not llm_category_ids and not instructions:
        return [], []
    user_prompt = build_user_prompt(
        pages, llm_category_ids, instructions, layout_aware=layout_aware
    )
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
            {"role": "system", "content": build_system_prompt(layout_aware)},
            {"role": "user", "content": user_prompt},
        ],
    }
    # ... rest unchanged
```

- [ ] **Step 4: Run new and existing Ollama tests**

Run:
- `uv run pytest tests/providers/test_ollama.py -v`
- `make typecheck`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/providers/ollama.py tests/providers/test_ollama.py
git commit -m "feat(ollama): forward layout_aware through detect_redactions"
```

---

### Task 11: `detect_redactions_chunked` accepts and forwards `layout_aware`

Add the keyword to the orchestrator and forward it on the single
`provider.detect_redactions` call.

**Files:**
- Modify: `src/kuroi/core/chunking.py:50-104`
- Test: `tests/core/test_chunking.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_chunking.py` (if `_StubProvider` doesn't
already exist as a fixture, follow the patterns in the existing tests
in this file — typically a fake provider records calls):

```python
def test_chunked_call_forwards_layout_aware_to_provider() -> None:
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import RetryPolicy

    received: list[bool] = []

    class _RecordingProvider:
        name = "recording"
        model = "test"

        def detect_redactions(
            self,
            pages: tuple[Page, ...],
            llm_category_ids: tuple[str, ...],
            *,
            instructions: tuple[str, ...] = (),
            seed: int | None = None,
            attempt: int = 0,
            layout_aware: bool = False,
        ) -> tuple[list[Finding], list[ChunkRecord]]:
            received.append(layout_aware)
            return [], [
                ChunkRecord(
                    chunk_idx=0,
                    pages=tuple(p.number for p in pages),
                    temperature=0.0,
                    seed_requested=None,
                    seed_honored=False,
                    system_fingerprint=None,
                    prompt_sha256="x",
                    response_sha256="y",
                    tokens_in=1,
                    tokens_out=1,
                    duration_ms=1,
                )
            ]

    pages = (
        Page(number=1, words=(Word(idx=0, text="a", bbox=(0, 0, 1, 1)),)),
        Page(number=2, words=(Word(idx=0, text="b", bbox=(0, 0, 1, 1)),)),
    )

    detect_redactions_chunked(
        _RecordingProvider(),
        pages,
        ("k",),
        pages_per_batch=1,
        retry_policy=RetryPolicy(max_retries=0, backoff=0.0, backoff_multiplier=2.0),
        layout_aware=True,
    )

    assert received == [True, True]
```

(If `Page`, `Word`, `Finding`, `ChunkRecord` aren't already imported at
the top of `tests/core/test_chunking.py`, add the imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_chunking.py::test_chunked_call_forwards_layout_aware_to_provider -v`
Expected: FAIL — `detect_redactions_chunked` doesn't accept `layout_aware`.

- [ ] **Step 3: Pre-flight — update existing test stubs**

Three existing test stubs implement `detect_redactions` without the
new `layout_aware` keyword. Once the orchestrator forwards it (next
step), every test using these stubs would crash with
`TypeError: detect_redactions() got an unexpected keyword argument 'layout_aware'`.
Add `layout_aware: bool = False` to each:

- `tests/core/test_chunking.py:53` — first stub.
- `tests/core/test_chunking.py:158` — second stub.
- `tests/cli/test_run_provider_wiring.py:23` — `_StubProvider`.

Each fix is the same one-line addition to the keyword list:

```python
def detect_redactions(
    self,
    pages: ...,
    llm_category_ids: ...,
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    attempt: int = 0,
    layout_aware: bool = False,  # NEW
) -> ...:
```

The bodies are unchanged; the stubs simply accept and ignore the new
keyword.

- [ ] **Step 4: Update the orchestrator**

In `src/kuroi/core/chunking.py`, replace the signature and the
provider call:

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
    layout_aware: bool = False,
    on_batch_start: Callable[[int, int, tuple[int, ...]], None] | None = None,
    on_batch_complete: Callable[[int, int, tuple[int, ...], ChunkRecord], None] | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
```

And inside the loop body, change the `provider.detect_redactions` call:

```python
findings, chunks = provider.detect_redactions(
    batch,
    llm_category_ids,
    instructions=instructions,
    seed=seed,
    attempt=attempt,
    layout_aware=layout_aware,
)
```

> **Note for the engineer:** if/when the subdivide-on-failure work
> (`docs/superpowers/plans/2026-05-08-subdivide-on-failure.md`) lands
> later and introduces a `_try_or_subdivide` helper, that helper will
> need to thread `layout_aware` through its recursion. That is *that*
> plan's responsibility, not this one.

- [ ] **Step 5: Run test to verify it passes + full suite**

Run:
- `uv run pytest tests/core/test_chunking.py -v`
- `make test`

Expected: PASS — both the new test, and the existing chunking + CLI
provider-wiring tests (whose stubs were updated in Step 3).

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py tests/cli/test_run_provider_wiring.py
git commit -m "feat(chunking): forward layout_aware to provider in orchestrator"
```

---

### Task 12: `Config` and `ConfigOverrides` gain `layout_aware`

Add the field to both dataclasses and resolve it from a new `[prompt]`
TOML table, with `ConfigOverrides.layout_aware` (the CLI value) winning
over the file. No env var support — matches the existing
`pages_per_batch` pattern.

**Files:**
- Modify: `src/kuroi/core/config.py`
- Test: `tests/core/test_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_config.py`:

```python
def test_config_layout_aware_default_false(tmp_path: Path) -> None:
    config = resolve_config(
        ConfigOverrides(),
        env={},
        file_path=tmp_path / "missing.toml",
    )
    assert config.layout_aware is False


def test_config_layout_aware_from_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[prompt]\nlayout_aware = true\n")

    config = resolve_config(
        ConfigOverrides(),
        env={},
        file_path=cfg_path,
    )
    assert config.layout_aware is True


def test_config_layout_aware_override_wins_over_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[prompt]\nlayout_aware = true\n")

    config = resolve_config(
        ConfigOverrides(layout_aware=False),
        env={},
        file_path=cfg_path,
    )
    assert config.layout_aware is False


def test_config_layout_aware_rejects_non_bool(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[prompt]\nlayout_aware = "yes"\n')

    with pytest.raises(ConfigError, match="layout_aware"):
        resolve_config(ConfigOverrides(), env={}, file_path=cfg_path)


def test_config_prompt_table_must_be_table(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("prompt = 42\n")

    with pytest.raises(ConfigError, match="prompt"):
        resolve_config(ConfigOverrides(), env={}, file_path=cfg_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_config.py -k "layout_aware or prompt_table" -v`
Expected: FAIL — `Config` has no `layout_aware`, `ConfigOverrides` has no `layout_aware`, `[prompt]` is unrecognized.

- [ ] **Step 3: Update the dataclasses**

In `src/kuroi/core/config.py`, extend `Config`:

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
```

Extend `ConfigOverrides`:

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
```

- [ ] **Step 4: Add the resolution helper and integrate**

Add a helper to `src/kuroi/core/config.py` (place near `_read_retry_policy`):

```python
def _read_layout_aware(
    file_data: dict[str, Any],
    overrides: ConfigOverrides,
) -> bool:
    """Resolve layout_aware as CLI override > file > default.

    Raises `ConfigError` if the file value is the wrong type.
    """
    prompt_table = file_data.get("prompt", {})
    if not isinstance(prompt_table, dict):
        raise ConfigError(
            f"Expected table for `prompt`, got {type(prompt_table).__name__}"
        )

    layout_aware: bool = False
    if "layout_aware" in prompt_table:
        raw = prompt_table["layout_aware"]
        if not isinstance(raw, bool):
            raise ConfigError(
                f"Expected boolean for `prompt.layout_aware`, got {type(raw).__name__}"
            )
        layout_aware = raw

    if overrides.layout_aware is not None:
        layout_aware = overrides.layout_aware

    return layout_aware
```

Then update the `Config(...)` construction at the end of `resolve_config`:

```python
return Config(
    provider=provider,
    model=model,
    ollama_url=ollama_url,
    audit_include_text=audit_include_text_raw,
    backup_retention_hours=backup_retention_raw,
    retry=_read_retry_policy(file_data, env, overrides),
    layout_aware=_read_layout_aware(file_data, overrides),
)
```

- [ ] **Step 5: Run tests to verify they pass + full suite**

Run:
- `uv run pytest tests/core/test_config.py -v`
- `make test`
- `make typecheck`

Expected: PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/config.py tests/core/test_config.py
git commit -m "feat(config): add prompt.layout_aware resolution from TOML and overrides"
```

---

### Task 13: CLI flag `--layout-aware` / `--no-layout-aware`

Add the typer option. The flag value flows into `ConfigOverrides`,
where Task 12 already resolved it.

**Files:**
- Modify: `src/kuroi/cli/run.py:44-118` (add the parameter), `src/kuroi/cli/run.py:159-176` (forward into `ConfigOverrides`)
- Test: `tests/cli/test_run.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_run_provider_wiring.py` (this file has the
established `_StubProvider` + `_fake_make` pattern; extend it). The
existing `_common_args` helper already passes `--rules pii` plus
backup/audit dirs — `pii` is the rule set referenced elsewhere in the
suite, and it reaches the provider:

```python
def test_run_cli_flag_layout_aware_propagates(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, bool] = {}

    class _SpyProvider:
        name = "stub"

        def __init__(self, model: str) -> None:
            self.model = model

        def detect_redactions(
            self,
            pages: Any,
            llm_category_ids: Any,
            *,
            instructions: tuple[str, ...] = (),
            seed: int | None = None,
            attempt: int = 0,
            layout_aware: bool = False,
        ) -> tuple[list[Finding], list[Any]]:
            from kuroi.core.audit_records import ChunkRecord

            captured["layout_aware"] = layout_aware
            return [], [
                ChunkRecord(
                    chunk_idx=0,
                    pages=tuple(p.number for p in pages),
                    temperature=0.0,
                    seed_requested=None,
                    seed_honored=False,
                    system_fingerprint=None,
                    prompt_sha256="a" * 64,
                    response_sha256="b" * 64,
                    tokens_in=1,
                    tokens_out=1,
                    duration_ms=1,
                )
            ]

    def _fake_make(cfg: Config) -> _SpyProvider:
        return _SpyProvider(model=cfg.model)

    monkeypatch.setattr("kuroi.cli.run.make_provider", _fake_make)

    pdf = make_pdf(["alice@example.com"])
    out = tmp_path / "out.pdf"

    result = CliRunner().invoke(
        app, [*_common_args(pdf, out, tmp_path), "--layout-aware"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["layout_aware"] is True


def test_run_no_layout_aware_flag_overrides_config_true(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--no-layout-aware on the CLI wins over [prompt] layout_aware=true in config."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "kuroi").mkdir()
    (cfg_dir / "kuroi" / "config.toml").write_text(
        "[prompt]\nlayout_aware = true\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))

    captured: dict[str, bool] = {}

    class _SpyProvider:
        name = "stub"

        def __init__(self, model: str) -> None:
            self.model = model

        def detect_redactions(
            self,
            pages: Any,
            llm_category_ids: Any,
            *,
            instructions: tuple[str, ...] = (),
            seed: int | None = None,
            attempt: int = 0,
            layout_aware: bool = False,
        ) -> tuple[list[Finding], list[Any]]:
            from kuroi.core.audit_records import ChunkRecord

            captured["layout_aware"] = layout_aware
            return [], [
                ChunkRecord(
                    chunk_idx=0,
                    pages=tuple(p.number for p in pages),
                    temperature=0.0,
                    seed_requested=None,
                    seed_honored=False,
                    system_fingerprint=None,
                    prompt_sha256="a" * 64,
                    response_sha256="b" * 64,
                    tokens_in=1,
                    tokens_out=1,
                    duration_ms=1,
                )
            ]

    monkeypatch.setattr(
        "kuroi.cli.run.make_provider", lambda cfg: _SpyProvider(model=cfg.model)
    )

    pdf = make_pdf(["alice@example.com"])
    out = tmp_path / "out.pdf"

    result = CliRunner().invoke(
        app, [*_common_args(pdf, out, tmp_path), "--no-layout-aware"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["layout_aware"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_run.py -k "layout_aware" -v`
Expected: FAIL — `--layout-aware` is not a recognized CLI flag.

- [ ] **Step 3: Add the CLI option**

In `src/kuroi/cli/run.py`, add a new typer parameter (place it next to
the other prompt-shaping options, e.g. after `pages_per_batch`):

```python
layout_aware: bool | None = typer.Option(
    None,
    "--layout-aware/--no-layout-aware",
    help=(
        "Wrap the LLM prompt with PyMuPDF block tags so the model can "
        "see paragraph and other layout boundaries. Default: off "
        "(or set [prompt] layout_aware in config)."
    ),
),
```

Then update the `ConfigOverrides(...)` construction (around line 162):

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
    ),
    env=os.environ,
    file_path=xdg_config_home() / "kuroi" / "config.toml",
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_run.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(cli): add --layout-aware flag for prompt-shaping"
```

---

### Task 14: CLI threads `config.layout_aware` to the orchestrator

Wire the resolved `Config.layout_aware` into the
`detect_redactions_chunked` call.

**Files:**
- Modify: `src/kuroi/cli/run.py:252-263` (the `detect_redactions_chunked` call)

- [ ] **Step 1: Update the call**

In `src/kuroi/cli/run.py`, the `detect_redactions_chunked` invocation
already exists. Add `layout_aware=config.layout_aware`:

```python
provider_findings, chunks = detect_redactions_chunked(
    provider,
    pages,
    tuple(llm_cat_ids),
    instructions=instruction_tuple,
    seed=seed,
    pages_per_batch=effective_batch_size,
    retry_policy=config.retry,
    layout_aware=config.layout_aware,
    on_batch_start=_on_batch_start if batched_ui else None,
    on_batch_complete=_on_batch_complete if batched_ui else None,
)
```

- [ ] **Step 2: Update the spy-provider test from Task 13**

Make the test from Task 13 actually drive the full CLI through this
call site (end-to-end), so the assertion is "fake provider observed
`layout_aware=True` because CLI flag set it via Config".

If the test from Task 13 was written to monkeypatch `make_provider` and
let the run reach `detect_redactions_chunked`, this step requires no
test change — Task 13's test should now pass end-to-end. Re-run it.

- [ ] **Step 3: Run all CLI tests + full suite**

Run:
- `uv run pytest tests/cli/test_run.py -v`
- `make test`
- `make typecheck`

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/kuroi/cli/run.py
git commit -m "feat(cli): pass config.layout_aware to detect_redactions_chunked"
```

---

### Task 15: Per-batch block-count logging at INFO

When `layout_aware=True`, append `blocks=N` to the per-batch progress
line so users can see structural-tag overhead in `-v` runs.

**Files:**
- Modify: `src/kuroi/cli/run.py:241-250` (the `_on_batch_complete` closure)
- Test: `tests/cli/test_run.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_run_provider_wiring.py` (the
`CliRunner().invoke` result captures stdout, which is what we assert
against):

```python
def test_run_logs_block_count_when_layout_aware(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--layout-aware adds `blocks=N` to the per-batch progress line.
    The batched-UI path requires `total_batches > 1`, so we use a
    multi-page PDF with --pages-per-batch 1."""

    def _fake_make(cfg: Config) -> _StubProvider:
        return _StubProvider(model=cfg.model)

    monkeypatch.setattr("kuroi.cli.run.make_provider", _fake_make)

    pdf = make_pdf(["First page", "Second page"])
    out = tmp_path / "out.pdf"

    result = CliRunner().invoke(
        app,
        [
            *_common_args(pdf, out, tmp_path),
            "--layout-aware",
            "--pages-per-batch", "1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "blocks=" in result.stdout


def test_run_does_not_log_block_count_without_layout_aware(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_make(cfg: Config) -> _StubProvider:
        return _StubProvider(model=cfg.model)

    monkeypatch.setattr("kuroi.cli.run.make_provider", _fake_make)

    pdf = make_pdf(["First page", "Second page"])
    out = tmp_path / "out.pdf"

    result = CliRunner().invoke(
        app,
        [
            *_common_args(pdf, out, tmp_path),
            "--pages-per-batch", "1",  # no --layout-aware
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "blocks=" not in result.stdout
```

> **Note for the engineer:** `_StubProvider` from earlier in this file
> doesn't yet accept `layout_aware`. After Task 13, you've already
> updated the spy provider used in those tests; for these tests you
> can add `layout_aware: bool = False` to the existing `_StubProvider`'s
> `detect_redactions` signature so it satisfies the updated Protocol.
> One-line change to the existing class.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_run.py -k "block_count" -v`
Expected: FAIL.

- [ ] **Step 3: Update the closure**

In `src/kuroi/cli/run.py`, replace `_on_batch_complete`:

```python
def _on_batch_complete(
    batch_idx: int,
    total: int,
    page_numbers: tuple[int, ...],
    chunk: ChunkRecord,
) -> None:
    extra = ""
    if config.layout_aware:
        block_total = sum(
            len({w.block_id for w in pages[pn - 1].words})
            for pn in page_numbers
        )
        extra = f" blocks={block_total}"
    console.print(
        f" done in {chunk.duration_ms} ms, "
        f"tokens_in={chunk.tokens_in} tokens_out={chunk.tokens_out}{extra}"
    )
```

- [ ] **Step 4: Run tests to verify they pass + full suite**

Run:
- `uv run pytest tests/cli/test_run.py -v`
- `make test`

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(cli): log per-batch block count when layout-aware"
```

---

### Task 16: Documentation

Update CHANGELOG, add a short prompt-tuning user guide, and update the
configuration reference. No code changes; one commit at the end.

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/reference/configuration.md`
- Create: `docs/user-guide/prompt-tuning.md`

- [ ] **Step 1: Update `CHANGELOG.md`**

Open `CHANGELOG.md`. Under the `[Unreleased]` section's `Added`
sub-heading (creating the section if it doesn't exist, matching the
file's existing style), append:

```markdown
- `--layout-aware` / `--no-layout-aware` flag (and `[prompt] layout_aware`
  config key) wraps the LLM prompt with PyMuPDF block boundaries
  (`<block id="N">…</block>`). Helps the model disambiguate field labels
  from values and reduces context loss across subdivided batches. Off by
  default. Adds ~5% prompt-token overhead on typical pages; the per-batch
  progress line shows `blocks=N` when on so users can measure the cost.
```

- [ ] **Step 2: Add `docs/user-guide/prompt-tuning.md`**

Create `docs/user-guide/prompt-tuning.md`:

```markdown
# Prompt tuning

kuroi sends each batch of pages to the LLM as a stream of word-indexed
tokens. By default, the prompt is a flat sequence:

```
<page n="1">
[0]Patient [1]Name [2]: [3]John [4]Doe
</page>
```

This is the simplest possible representation, and it works. But on
documents with strong layout structure — forms, tables, recurring
headers — the LLM sometimes struggles to tell a field label from its
value, or treats the same recurring footer differently across pages.

## `--layout-aware`

Pass `--layout-aware` (or set `[prompt] layout_aware = true` in your
config) to wrap each page's words in `<block>` tags reflecting
PyMuPDF's layout analysis:

```
<page n="1">
<block id="3">[0]Patient [1]Name [2]:</block>
<block id="4">[3]John [4]Doe</block>
</page>
```

The model still reports findings as `(page, start, end)` word ranges —
the response schema is unchanged. The block tags are advisory metadata
that the model uses to disambiguate which words belong together.

### When to enable

- **Forms with field labels next to values.** Patient records, intake
  forms, court filings.
- **Documents with recurring headers/footers.** Multi-page legal
  documents, financial reports.
- **Documents where the LLM has been mis-redacting structural cues**
  (column headers, signature lines, page numbers) as PII.

### When it doesn't help much

- **Dense narrative prose.** Court opinions, contract bodies. Layout
  structure adds little signal in long unbroken paragraphs.
- **OCR'd pages.** OCR typically produces a single block per page, so
  the layout-aware prompt collapses to the flat shape with one extra
  wrapper.

### Cost

Block tags add roughly 5% prompt-token overhead on a typical page. The
per-batch progress line shows `blocks=N` when layout-aware mode is on,
so you can see the cost in your own runs:

```
  Batch 4/12 (pages 16-20)... done in 1.2s, tokens_in=2871 tokens_out=412 blocks=12
```

### Current limits

This first ship covers paragraph-level block boundaries. Two related
improvements are tracked in separate design docs:

- **Table-aware overlay** (column/row tags inside `<block type="table">`)
  — particularly helpful for column-vs-name disambiguation in dense
  tables. Not yet shipped.
- **Cross-page recurring-element deduplication** — emit recurring
  headers/footers once and reference back on subsequent pages. Larger
  token-savings win for repetitive documents. Not yet shipped.
```

- [ ] **Step 3: Update `docs/reference/config.md`**

In `docs/reference/config.md`, add a `[prompt]` section, matching the
format of the existing sections (likely `[retry]` or `[ollama]` for
the template). Insert near the other tables:

```markdown
## `[prompt]`

Prompt-shaping options.

| Key            | Type    | Default | Description                                                                 |
|----------------|---------|---------|-----------------------------------------------------------------------------|
| `layout_aware` | boolean | `false` | Wrap the LLM prompt with PyMuPDF block boundaries. See [Prompt tuning](../user-guide/prompt-tuning.md). |

CLI flag override: `--layout-aware` / `--no-layout-aware`.
```

- [ ] **Step 4: Register the new page in the docs nav**

In `zensical.toml`, the user-guide nav is explicit. Add an entry for
the new page (insert near the other user-guide entries; placement
between "LLM providers" and "Audit, diff, undo & backups" matches
the conceptual ordering of "configure a run" → "review a run"):

```toml
{ Install = "user-guide/install.md" },
{ "Quick start" = "user-guide/quickstart.md" },
{ "Batch redaction" = "user-guide/batch.md" },
{ "LLM providers" = "user-guide/providers.md" },
{ "Prompt tuning" = "user-guide/prompt-tuning.md" },
{ "Audit, diff, undo & backups" = "user-guide/audit-and-undo.md" },
{ Troubleshooting = "user-guide/troubleshooting.md" },
```

- [ ] **Step 5: Build the docs in strict mode**

Run: `make docs-build`
Expected: PASS — the nav resolves the new page; no broken links.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md docs/user-guide/prompt-tuning.md docs/reference/config.md zensical.toml
git commit -m "docs: document --layout-aware flag and prompt-tuning guide"
```

---

## Final verification

After all 16 tasks are committed, run the full verification chain and
sanity-check the end-to-end behavior on a real document.

- [ ] **Step 1: Full test, typecheck, lint, docs**

Run:
- `make test`
- `make typecheck`
- `make lint`
- `make docs-build`

Expected: all green.

- [ ] **Step 2: Smoke-test on a real document**

Run on a small fixture PDF (use any 1-3 page PDF in `tests/fixtures/`,
or create one with `make_pdf`):

```bash
uv run kuroi run path/to/test.pdf -o /tmp/out.pdf --instruct "redact names" --layout-aware -v
```

Expected: command succeeds; the verbose output includes a `blocks=N`
counter on the per-batch progress line; the run completes; `/tmp/out.pdf`
is written.

- [ ] **Step 3: Compare prompts on/off (optional)**

For confidence: run the same document twice, once with
`--layout-aware` and once with `--no-layout-aware`, both at `-v -v`
(DEBUG) so the full prompt is logged. Visually inspect that:

- The off-run prompt has no `<block>` tags.
- The on-run prompt has `<block id="N">…</block>` sections wrapping
  groups of `[idx]token` markers in document order.
- Both runs produce identical findings on a simple narrative document
  (where layout structure shouldn't change semantic decisions). On a
  form-style document, they may differ — that is the expected payoff.

- [ ] **Step 4: Push the branch / open a PR per project convention**
