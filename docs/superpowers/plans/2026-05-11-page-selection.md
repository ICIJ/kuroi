# Page Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--pages` flag to `kuroi run` that restricts the entire pipeline (extraction, OCR, regex, LLM, redaction) to a user-specified subset of pages; unselected pages pass through unchanged.

**Architecture:** A new `core/page_selection.py` parses `1,3-5,10`-style specs into a sorted, deduped, frozen `PageSelection`. `core/pdf.py` accepts that selection to skip extraction and OCR on unselected pages. `cli/run.py` parses/validates the selection, threads it through extraction, replaces two index-based page lookups with a dict, and records the selection in the audit header. `core/redaction.py` needs no change — it already keys by page number and skips pages without findings, so unselected pages pass through naturally.

**Tech Stack:** Python 3.12, Typer CLI, PyMuPDF, pytest, frozen `@dataclass`.

**Spec:** `docs/superpowers/specs/2026-05-11-page-selection-design.md`

---

## File Structure

**Create:**
- `src/kuroi/core/page_selection.py` — `PageSelection` dataclass, `PageSelectionError`, `parse()`, `validate()`. Pure parsing/validation, no I/O, no other-module imports.
- `tests/core/test_page_selection.py` — parse + validate tests.
- `tests/core/test_pdf_selection.py` — tests for the new `selection` parameter on `extract_word_index`.

**Modify:**
- `src/kuroi/core/pdf.py` — add `total_pages: int` to `ExtractionResult`; add `selection: PageSelection | None = None` kw-only param to `extract_word_index`; add `index_by_number()` helper.
- `src/kuroi/core/audit.py` — `AuditLog.open()` accepts optional `pages_spec` and `pages_resolved`.
- `src/kuroi/cli/run.py` — new `--pages` Typer option; parse before I/O, validate after extraction; build `pages_by_number` and replace two `pages[N-1]` lookups; extend cost line; pass audit fields.
- `tests/cli/test_run.py` — update one existing `ExtractionResult(...)` construction to include `total_pages`; add 4 new tests for `--pages` behavior.

No other files are touched. `core/redaction.py`, `core/chunking.py`, and provider code work as-is.

---

## Task 1: Parse `--pages` spec into `PageSelection`

**Files:**
- Create: `src/kuroi/core/page_selection.py`
- Test: `tests/core/test_page_selection.py`

- [ ] **Step 1.1: Write the failing tests for parse**

Create `tests/core/test_page_selection.py`:

```python
"""Tests for --pages spec parsing and validation."""

from __future__ import annotations

import pytest

from kuroi.core.page_selection import PageSelection, PageSelectionError, parse


def test_parse_single() -> None:
    result = parse("5")
    assert result.pages == (5,)
    assert result.raw == "5"


def test_parse_list() -> None:
    result = parse("1,2,14")
    assert result.pages == (1, 2, 14)
    assert result.raw == "1,2,14"


def test_parse_range() -> None:
    result = parse("1-5")
    assert result.pages == (1, 2, 3, 4, 5)


def test_parse_mixed() -> None:
    result = parse("1,3-5,10")
    assert result.pages == (1, 3, 4, 5, 10)


def test_parse_whitespace_tolerant() -> None:
    result = parse("  1 , 3 - 5 ")
    assert result.pages == (1, 3, 4, 5)
    # raw preserves the user's original string verbatim
    assert result.raw == "  1 , 3 - 5 "


def test_parse_sorts_and_dedupes() -> None:
    result = parse("3,1,2,3")
    assert result.pages == (1, 2, 3)


def test_parse_rejects_empty() -> None:
    with pytest.raises(PageSelectionError, match="empty"):
        parse("")


def test_parse_rejects_whitespace_only() -> None:
    with pytest.raises(PageSelectionError, match="empty"):
        parse("   ")


def test_parse_rejects_zero() -> None:
    with pytest.raises(PageSelectionError, match=">= 1"):
        parse("0")


def test_parse_rejects_negative() -> None:
    with pytest.raises(PageSelectionError, match=">= 1"):
        parse("-3")


def test_parse_rejects_reversed_range() -> None:
    with pytest.raises(PageSelectionError, match="reversed"):
        parse("5-1")


def test_parse_rejects_open_range_trailing() -> None:
    with pytest.raises(PageSelectionError, match="open ranges"):
        parse("1-")


def test_parse_rejects_open_range_leading() -> None:
    with pytest.raises(PageSelectionError, match="open ranges"):
        parse("-5")


def test_parse_rejects_non_numeric() -> None:
    with pytest.raises(PageSelectionError, match="not a number"):
        parse("1,abc")


def test_page_selection_contains() -> None:
    selection = parse("1,3-5,10")
    assert 1 in selection
    assert 4 in selection
    assert 10 in selection
    assert 2 not in selection
    assert 11 not in selection


def test_page_selection_len_and_iter() -> None:
    selection = parse("1,3-5,10")
    assert len(selection) == 5
    assert list(selection) == [1, 3, 4, 5, 10]
```

- [ ] **Step 1.2: Run the failing tests**

Run: `pytest tests/core/test_page_selection.py -v`

Expected: All tests FAIL with `ModuleNotFoundError: No module named 'kuroi.core.page_selection'`.

- [ ] **Step 1.3: Implement the parser**

Create `src/kuroi/core/page_selection.py`:

```python
"""Parse and validate ``--pages`` specs for ``kuroi run``.

The spec grammar accepts comma-separated singles and inclusive ranges, plus
arbitrary whitespace:

    SPEC := PART ("," PART)*
    PART := INT | INT "-" INT

Examples (all valid):
    "1", "1,2,14", "1-5", "1,3-5,10", "  1 , 3 - 5 "

Rejected (with ``PageSelectionError``):
    "", "0", "-3", "5-1", "1-", "-5", "1,abc"
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


class PageSelectionError(ValueError):
    """Raised on malformed --pages input or out-of-range pages."""


@dataclass(frozen=True)
class PageSelection:
    raw: str
    pages: tuple[int, ...]

    def __contains__(self, n: object) -> bool:
        return n in self.pages

    def __iter__(self) -> Iterator[int]:
        return iter(self.pages)

    def __len__(self) -> int:
        return len(self.pages)


def parse(spec: str) -> PageSelection:
    """Parse a ``--pages`` spec. Does not validate against any document."""
    if not spec.strip():
        raise PageSelectionError("empty page selection")

    collected: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            raise PageSelectionError(f"empty part in {spec!r}")
        if "-" in token:
            _add_range(token, collected)
        else:
            collected.add(_parse_int(token))

    return PageSelection(raw=spec, pages=tuple(sorted(collected)))


def _add_range(token: str, collected: set[int]) -> None:
    left, _, right = token.partition("-")
    left, right = left.strip(), right.strip()
    if not left or not right:
        raise PageSelectionError(
            f"open ranges not supported: {token!r} (use explicit start and end)"
        )
    start = _parse_int(left)
    end = _parse_int(right)
    if start > end:
        raise PageSelectionError(f"range {start}-{end} is reversed")
    collected.update(range(start, end + 1))


def _parse_int(token: str) -> int:
    try:
        n = int(token)
    except ValueError as exc:
        raise PageSelectionError(f"not a number: {token!r}") from exc
    if n < 1:
        raise PageSelectionError(f"page numbers must be >= 1, got {n}")
    return n
```

- [ ] **Step 1.4: Run the tests and verify they pass**

Run: `pytest tests/core/test_page_selection.py -v`

Expected: All 16 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add src/kuroi/core/page_selection.py tests/core/test_page_selection.py
git commit -m "feat(page-selection): parse --pages specs into PageSelection"
```

---

## Task 2: Validate `PageSelection` against a document's page count

**Files:**
- Modify: `src/kuroi/core/page_selection.py`
- Test: `tests/core/test_page_selection.py`

- [ ] **Step 2.1: Append the failing validate tests**

Append to `tests/core/test_page_selection.py`:

```python
from kuroi.core.page_selection import validate


def test_validate_passes_when_in_range() -> None:
    selection = parse("1,5,10")
    # Returns the same selection unchanged.
    result = validate(selection, page_count=10)
    assert result is selection


def test_validate_rejects_out_of_range_single() -> None:
    selection = parse("99")
    with pytest.raises(PageSelectionError, match="99 not in document"):
        validate(selection, page_count=10)


def test_validate_rejects_out_of_range_partial() -> None:
    # No silent trim — even if 1 and 2 are valid, the presence of 99
    # is a hard error.
    selection = parse("1,2,99")
    with pytest.raises(PageSelectionError, match="99 not in document"):
        validate(selection, page_count=10)


def test_validate_rejects_out_of_range_partial_range() -> None:
    selection = parse("1-15")
    with pytest.raises(PageSelectionError) as exc_info:
        validate(selection, page_count=10)
    # Message names the out-of-range pages and the document size
    msg = str(exc_info.value)
    assert "11" in msg
    assert "15" in msg
    assert "1-10" in msg


def test_validate_message_includes_document_size() -> None:
    selection = parse("99")
    with pytest.raises(PageSelectionError, match=r"1-10"):
        validate(selection, page_count=10)
```

- [ ] **Step 2.2: Run the failing tests**

Run: `pytest tests/core/test_page_selection.py -v -k validate`

Expected: 5 tests FAIL with `ImportError: cannot import name 'validate'`.

- [ ] **Step 2.3: Implement validate**

Append to `src/kuroi/core/page_selection.py`:

```python
def validate(selection: PageSelection, *, page_count: int) -> PageSelection:
    """Ensure every page in ``selection`` falls in ``[1, page_count]``.

    Raises ``PageSelectionError`` listing the out-of-range pages if any
    exist. Out-of-range is always a hard error — there is no silent trim.

    Returns the same selection (for fluent use at call sites).
    """
    bad = [p for p in selection.pages if p > page_count]
    if bad:
        bad_str = _format_pages(bad)
        raise PageSelectionError(
            f"page(s) {bad_str} not in document (1-{page_count})"
        )
    return selection


def _format_pages(pages: list[int]) -> str:
    """Compact representation of a sorted page list: contiguous runs as ranges."""
    if not pages:
        return ""
    runs: list[str] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    runs.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(runs)
```

Note the keyword-only `page_count` parameter — this avoids ambiguity at call sites and matches the existing style of `extract_word_index(pdf, *, selection=...)` that Task 3 introduces.

Update the call in test_validate_passes_when_in_range to pass `page_count=10` as keyword (already keyword in the test above). The other `validate` calls in Step 2.1 already use the keyword form — re-check by re-reading the appended block; if any positional call slipped in, fix it now.

- [ ] **Step 2.4: Run the tests and verify they pass**

Run: `pytest tests/core/test_page_selection.py -v`

Expected: All 21 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add src/kuroi/core/page_selection.py tests/core/test_page_selection.py
git commit -m "feat(page-selection): validate selection against document page count"
```

---

## Task 3: Extend `ExtractionResult` with `total_pages` and add `index_by_number` helper

This is the smallest possible step on `pdf.py`. It is split out from Task 4 so that the
`ExtractionResult` schema change lands atomically and a single failing test in `tests/cli/test_run.py`
gets fixed in the same commit.

**Files:**
- Modify: `src/kuroi/core/pdf.py`
- Modify: `tests/cli/test_run.py:644` (existing `ExtractionResult(...)` construction)
- Test: `tests/core/test_pdf.py`

- [ ] **Step 3.1: Append the failing test for `total_pages`**

Append to `tests/core/test_pdf.py`:

```python
def test_extract_word_index_reports_total_pages(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["page one", "page two", "page three"])
    result = extract_word_index(pdf)
    assert result.total_pages == 3
```

- [ ] **Step 3.2: Append the failing test for `index_by_number`**

Append to `tests/core/test_pdf.py`:

```python
def test_index_by_number_maps_pages_by_1indexed_number(
    make_pdf: Callable[..., Path],
) -> None:
    from kuroi.core.pdf import index_by_number

    pdf = make_pdf(["one", "two", "three"])
    result = extract_word_index(pdf)
    lookup = index_by_number(result.pages)

    assert set(lookup.keys()) == {1, 2, 3}
    for num, page in lookup.items():
        assert page.number == num
```

- [ ] **Step 3.3: Run the failing tests**

Run: `pytest tests/core/test_pdf.py::test_extract_word_index_reports_total_pages tests/core/test_pdf.py::test_index_by_number_maps_pages_by_1indexed_number -v`

Expected: Both FAIL — first with `AttributeError: 'ExtractionResult' object has no attribute 'total_pages'`, second with `ImportError`.

- [ ] **Step 3.4: Add `total_pages` field and `index_by_number` helper**

In `src/kuroi/core/pdf.py`, modify the `ExtractionResult` dataclass:

```python
@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[Page, ...]
    ocr_page_count: int  # 0 if no OCR was needed
    total_pages: int     # doc.page_count, regardless of any --pages selection
```

In the same file, modify the single `ExtractionResult(...)` construction inside `extract_word_index` (currently the final return statement before `finally:`) to pass `total_pages`:

```python
        return ExtractionResult(
            pages=tuple(pages),
            ocr_page_count=len(scan_candidates),
            total_pages=doc.page_count,
        )
```

Append a new helper at the bottom of the file (after `serialize_for_llm`):

```python
def index_by_number(pages: tuple[Page, ...]) -> dict[int, Page]:
    """Map 1-indexed page number → Page.

    Useful wherever callers want to look up a page by its document-side
    number rather than its position in the tuple. With ``--pages`` in
    effect, the tuple is no longer 1-indexed-dense, so positional access
    breaks; this helper is the safe replacement.
    """
    return {p.number: p for p in pages}
```

- [ ] **Step 3.5: Fix the existing test_run.py construction**

In `tests/cli/test_run.py:644`, update the existing `ExtractionResult(...)` call inside `test_run_prints_ocr_notice_when_scanned_pages_found` to include the new field:

```python
    monkeypatch.setattr(
        "kuroi.cli.run.extract_word_index",
        lambda path: ExtractionResult(
            pages=(Page(number=1, words=()),),
            ocr_page_count=2,
            total_pages=1,
        ),
    )
```

This is the only construction site in `tests/` (verified via grep). The production construction inside `extract_word_index` was already updated in step 3.4.

- [ ] **Step 3.6: Run the full test suite to confirm no regressions**

Run: `pytest tests/core/test_pdf.py tests/cli/test_run.py -v`

Expected: All tests PASS, including the two new ones from steps 3.1–3.2 and the updated `test_run_prints_ocr_notice_when_scanned_pages_found`.

- [ ] **Step 3.7: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py tests/cli/test_run.py
git commit -m "feat(pdf): add total_pages to ExtractionResult and index_by_number helper"
```

---

## Task 4: Pass `selection` to `extract_word_index`; gate extraction and OCR

**Files:**
- Modify: `src/kuroi/core/pdf.py`
- Test: `tests/core/test_pdf_selection.py`

- [ ] **Step 4.1: Write the failing tests for selection-aware extraction**

Create `tests/core/test_pdf_selection.py`:

```python
"""Tests for the selection-aware path of ``extract_word_index``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from kuroi.core.page_selection import parse
from kuroi.core.pdf import OcrRequiredError, extract_word_index


def test_extract_word_index_filters_to_selection(
    make_pdf: Callable[..., Path],
) -> None:
    pdf = make_pdf(["one", "two", "three", "four", "five"])
    selection = parse("2,4")

    result = extract_word_index(pdf, selection=selection)

    assert tuple(p.number for p in result.pages) == (2, 4)
    assert result.total_pages == 5
    assert result.ocr_page_count == 0


def test_extract_word_index_preserves_word_indices_under_selection(
    make_pdf: Callable[..., Path],
) -> None:
    # The Word.idx field is per-page-0-based regardless of which pages
    # were selected — the LLM protocol depends on this invariant.
    pdf = make_pdf(["alpha beta gamma", "delta epsilon"])
    selection = parse("2")

    result = extract_word_index(pdf, selection=selection)

    page = result.pages[0]
    assert page.number == 2
    assert tuple(w.idx for w in page.words) == (0, 1)
    assert tuple(w.text for w in page.words) == ("delta", "epsilon")


def test_extract_word_index_skips_ocr_check_on_unselected_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Build a 2-page PDF: page 1 native text, page 2 image-only.
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Hello world", fontsize=11)
    page2 = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 128, 128))
    pix.clear_with(200)
    page2.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    pdf = tmp_path / "mixed.pdf"
    doc.save(str(pdf))
    doc.close()

    # Tesseract is unavailable.
    monkeypatch.setattr("kuroi.core.pdf.shutil.which", lambda name: None)
    selection = parse("1")

    # No OcrRequiredError because page 2 (the scan candidate) was not selected.
    result = extract_word_index(pdf, selection=selection)

    assert tuple(p.number for p in result.pages) == (1,)
    assert result.ocr_page_count == 0
    assert result.total_pages == 2


def test_extract_word_index_raises_ocr_when_selected_page_needs_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same 2-page fixture as above, but select page 2 → must raise.
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Hello world", fontsize=11)
    page2 = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 128, 128))
    pix.clear_with(200)
    page2.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    pdf = tmp_path / "mixed.pdf"
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr("kuroi.core.pdf.shutil.which", lambda name: None)
    selection = parse("2")

    with pytest.raises(OcrRequiredError) as exc_info:
        extract_word_index(pdf, selection=selection)

    assert exc_info.value.page_numbers == (2,)


def test_extract_word_index_no_selection_unchanged_behavior(
    make_pdf: Callable[..., Path],
) -> None:
    # Without a selection, behavior matches the legacy contract.
    pdf = make_pdf(["one", "two", "three"])

    result = extract_word_index(pdf)

    assert tuple(p.number for p in result.pages) == (1, 2, 3)
    assert result.total_pages == 3
```

- [ ] **Step 4.2: Run the failing tests**

Run: `pytest tests/core/test_pdf_selection.py -v`

Expected: First four tests FAIL with `TypeError: extract_word_index() got an unexpected keyword argument 'selection'`. The last test passes (no selection used).

- [ ] **Step 4.3: Wire `selection` through `extract_word_index`**

In `src/kuroi/core/pdf.py`:

1. Add the import for `PageSelection` at the top of the file (after the existing `import pymupdf`):

```python
from kuroi.core.page_selection import PageSelection
```

2. Change the function signature:

```python
def extract_word_index(
    pdf_path: Path,
    *,
    selection: PageSelection | None = None,
) -> ExtractionResult:
```

3. Update the docstring (replace the existing docstring on `extract_word_index`):

```python
    """Extract every word on every (selected) page with its bounding box.

    When ``selection`` is given, only the listed page numbers are
    extracted, and OCR scan-candidate detection runs only for those
    pages. The returned ``ExtractionResult.pages`` tuple contains only
    selected pages (preserving their original 1-indexed ``number``).
    ``total_pages`` always reflects the full document page count.

    Any selected page with at least one embedded image is treated as a
    scan candidate so OCR can recover text drawn inside the image. If
    any scan candidates are found and tesseract is not in PATH, raises
    OcrRequiredError listing those pages.
    """
```

4. Gate extraction inside the existing loop. Replace this block:

```python
        for page_idx in range(doc.page_count):
            pdf_page = doc[page_idx]
            raw = pdf_page.get_text("words")  # type: ignore[no-untyped-call]
            words = tuple(
                Word(
                    idx=i,
                    text=str(w[4]),
                    bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
                    block_id=int(w[5]),
                )
                for i, w in enumerate(raw)
            )
            pages.append(Page(number=page_idx + 1, words=words))
            if pdf_page.get_images():  # type: ignore[no-untyped-call]
                scan_candidates.append(page_idx + 1)
```

…with this (the new `continue` short-circuit is the only behavioral change):

```python
        for page_idx in range(doc.page_count):
            page_num = page_idx + 1
            if selection is not None and page_num not in selection:
                continue
            pdf_page = doc[page_idx]
            raw = pdf_page.get_text("words")  # type: ignore[no-untyped-call]
            words = tuple(
                Word(
                    idx=i,
                    text=str(w[4]),
                    bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
                    block_id=int(w[5]),
                )
                for i, w in enumerate(raw)
            )
            pages.append(Page(number=page_num, words=words))
            if pdf_page.get_images():  # type: ignore[no-untyped-call]
                scan_candidates.append(page_num)
```

The OCR block immediately below this loop already iterates `scan_candidates`, which is now correctly pruned. But it still uses `pages[page_1idx - 1]` for OCR result assignment, which is broken under selection (the list is no longer 1-indexed-dense).

Replace the OCR block:

```python
        if scan_candidates:
            if shutil.which("tesseract") is None:
                raise OcrRequiredError(tuple(scan_candidates))
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

…with this (look up the slot by page number rather than by index):

```python
        if scan_candidates:
            if shutil.which("tesseract") is None:
                raise OcrRequiredError(tuple(scan_candidates))
            slot_by_number = {p.number: idx for idx, p in enumerate(pages)}
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
                pages[slot_by_number[page_1idx]] = Page(number=page_1idx, words=words)
```

The `ExtractionResult(...)` return statement (already updated in Task 3 to pass `total_pages=doc.page_count`) is unchanged.

- [ ] **Step 4.4: Run the new and existing tests**

Run: `pytest tests/core/test_pdf.py tests/core/test_pdf_selection.py -v`

Expected: All tests PASS — both the new selection tests and the existing OCR tests (which exercise the same OCR block that just got rewritten).

- [ ] **Step 4.5: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf_selection.py
git commit -m "feat(pdf): accept selection to gate extraction and OCR"
```

---

## Task 5: Add page-selection fields to the audit header

**Files:**
- Modify: `src/kuroi/core/audit.py`
- Test: `tests/core/test_audit.py`

- [ ] **Step 5.1: Append a failing test for the new audit fields**

Append to `tests/core/test_audit.py`:

```python
def test_audit_log_records_page_selection(tmp_path: Path) -> None:
    """When --pages is in effect, the session_start header captures
    both the raw flag value and the resolved page set."""
    from kuroi.core.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog.open(
        path,
        original=tmp_path / "in.pdf",
        output=tmp_path / "out.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=(),
        session_id="abc",
        input_sha256="0" * 64,
        input_pages=5,
        input_bytes=100,
        model_version="claude-opus-4-7",
        pages_spec="1-3,5",
        pages_resolved=(1, 2, 3, 5),
    )
    log.close(verification_passed=True, redaction_count=0)

    lines = path.read_text().splitlines()
    import json as _json

    header = _json.loads(lines[0])
    assert header["event"] == "session_start"
    assert header["pages_spec"] == "1-3,5"
    assert header["pages_resolved"] == [1, 2, 3, 5]


def test_audit_log_omits_page_selection_when_unset(tmp_path: Path) -> None:
    """Default behavior (no --pages) leaves both fields absent so existing
    audit consumers see no schema change."""
    from kuroi.core.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog.open(
        path,
        original=tmp_path / "in.pdf",
        output=tmp_path / "out.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=(),
        session_id="abc",
        input_sha256="0" * 64,
        input_pages=5,
        input_bytes=100,
        model_version="claude-opus-4-7",
    )
    log.close(verification_passed=True, redaction_count=0)

    lines = path.read_text().splitlines()
    import json as _json

    header = _json.loads(lines[0])
    assert "pages_spec" not in header
    assert "pages_resolved" not in header
```

- [ ] **Step 5.2: Run the failing tests**

Run: `pytest tests/core/test_audit.py::test_audit_log_records_page_selection tests/core/test_audit.py::test_audit_log_omits_page_selection_when_unset -v`

Expected: First test FAILS with `TypeError: open() got an unexpected keyword argument 'pages_spec'`. Second passes (the keyword args don't exist yet so nothing is written).

- [ ] **Step 5.3: Wire the new fields through `AuditLog.open`**

In `src/kuroi/core/audit.py`, modify the `open()` classmethod signature to accept the two new optional fields (add them at the end of the parameter list, after `config_resolved_from`):

```python
    @classmethod
    def open(
        cls,
        path: Path,
        *,
        original: Path,
        output: Path,
        provider: str,
        model: str,
        rules: tuple[str, ...],
        session_id: str,
        input_sha256: str,
        input_pages: int,
        input_bytes: int,
        model_version: str,
        instructions: tuple[dict[str, Any], ...] = (),
        config_resolved_from: tuple[str, ...] = (),
        pages_spec: str | None = None,
        pages_resolved: tuple[int, ...] = (),
    ) -> AuditLog:
```

Inside the method, after the existing `log._write({...})` call that emits the `session_start` event, insert the conditional fields *inside* the dict literal. The cleanest way: build the payload dict, then add the optional fields, then write. Replace the existing single-call write:

```python
        log._write(
            {
                "event": "session_start",
                "audit_schema_version": 1,
                ...
                "config_resolved_from": list(config_resolved_from),
            }
        )
```

…with a build-then-write pattern that adds the fields only when set:

```python
        payload: dict[str, Any] = {
            "event": "session_start",
            "audit_schema_version": 1,
            "session_id": session_id,
            "ts_start": _now_iso(),
            "kuroi_version": _kuroi_version(),
            "input_path": str(original),
            "input_sha256": input_sha256,
            "input_pages": input_pages,
            "input_bytes": input_bytes,
            "output_path": str(output),
            "provider": provider,
            "model": model,
            "model_version": model_version,
            "rules": list(rules),
            "instructions": list(instructions),
            "config_resolved_from": list(config_resolved_from),
        }
        if pages_spec is not None:
            payload["pages_spec"] = pages_spec
            payload["pages_resolved"] = list(pages_resolved)
        log._write(payload)
```

The "absent when None" pattern keeps default audit records byte-for-byte identical to before, so no existing audit-consumer test breaks.

- [ ] **Step 5.4: Run the tests**

Run: `pytest tests/core/test_audit.py -v`

Expected: All tests PASS, including the two new ones.

- [ ] **Step 5.5: Commit**

```bash
git add src/kuroi/core/audit.py tests/core/test_audit.py
git commit -m "feat(audit): record raw + resolved page selection in session header"
```

---

## Task 6: Wire `--pages` into `kuroi run`

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Test: `tests/cli/test_run.py`

- [ ] **Step 6.1: Write failing CLI integration tests**

Append to `tests/cli/test_run.py`:

```python
def test_run_with_pages_processes_only_selected(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    """--pages restricts the LLM call to selected pages; output retains
    the full page count; audit header records the selection."""
    import json as _json

    pdf = make_pdf(["page one alice@example.com", "page two", "page three", "page four"])
    out = tmp_path / "redacted.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "--pages",
            "1-2",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    # Output PDF retains all four pages.
    doc = pymupdf.open(str(out))
    try:
        assert doc.page_count == 4
    finally:
        doc.close()

    # Audit header captures the selection.
    audit_files = list((tmp_path / "audit").glob("*.jsonl"))
    assert len(audit_files) == 1
    header = _json.loads(audit_files[0].read_text().splitlines()[0])
    assert header["pages_spec"] == "1-2"
    assert header["pages_resolved"] == [1, 2]


def test_run_with_pages_and_batch_chunks_selection_contiguously(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    """--pages combined with --pages-per-batch produces contiguous
    batches over the selection (gaps in the selection don't break
    batches)."""
    import json as _json

    # 8-page doc.
    pages_text = [f"page {i}" for i in range(1, 9)]
    pdf = make_pdf(pages_text)
    out = tmp_path / "redacted.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact nothing",
            "--pages",
            "1-3,6,8",
            "--pages-per-batch",
            "2",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout

    # Each batch becomes one chunk_request event; the union of `pages`
    # fields must equal the resolved selection, and batches sized 2.
    audit_files = list((tmp_path / "audit").glob("*.jsonl"))
    assert len(audit_files) == 1
    lines = audit_files[0].read_text().splitlines()
    chunk_events = [_json.loads(ln) for ln in lines if _json.loads(ln)["event"] == "chunk_request"]
    batches = [tuple(ev["pages"]) for ev in chunk_events]
    # Selected pages are [1, 2, 3, 6, 8]; with pages-per-batch=2 the
    # contiguous batches over the selection are:
    #   [1, 2]  [3, 6]  [8]
    assert batches == [(1, 2), (3, 6), (8,)]


def test_run_rejects_out_of_range_pages(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    pdf = make_pdf(["one", "two", "three"])

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact nothing",
            "--pages",
            "1,99",
            "-y",
            "--in-place",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 2
    assert "99" in result.stdout
    assert "1-3" in result.stdout  # references the document page range


def test_run_rejects_malformed_pages(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    pdf = make_pdf(["one", "two"])

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact nothing",
            "--pages",
            "abc",
            "-y",
            "--in-place",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 2
    assert "not a number" in result.stdout
```

- [ ] **Step 6.2: Run the failing tests**

Run: `pytest tests/cli/test_run.py -v -k "pages_processes_only_selected or pages_and_batch_chunks or rejects_out_of_range_pages or rejects_malformed_pages"`

Expected: All four FAIL — most with `No such option: --pages` since the flag isn't wired yet.

- [ ] **Step 6.3: Add the `--pages` option and wire parse + validate**

In `src/kuroi/cli/run.py`, update the imports at the top to add the new modules:

```python
from kuroi.core.page_selection import (
    PageSelection,
    PageSelectionError,
    parse as parse_page_selection,
    validate as validate_page_selection,
)
from kuroi.core.pdf import (
    OcrRequiredError,
    extract_word_index,
    index_by_number,
    serialize_for_llm,
)
```

(Update the existing `from kuroi.core.pdf import OcrRequiredError, extract_word_index, serialize_for_llm` line to include `index_by_number`.)

Add the new Typer option to `run()`'s parameter list, immediately after `pages_per_batch`:

```python
    pages_spec: str | None = typer.Option(
        None,
        "--pages",
        help=(
            "Process only the listed pages. Comma-separated list of "
            "page numbers and/or inclusive ranges (e.g. `1`, `1,2,14`, "
            "`1-5`, `1,3-5,10`). Default: all pages."
        ),
    ),
```

Inside the function body, immediately before the `if backup_dir is None:` block (so the parse happens before any I/O), insert:

```python
    selection: PageSelection | None = None
    if pages_spec is not None:
        try:
            selection = parse_page_selection(pages_spec)
        except PageSelectionError as exc:
            console.print(f"  [red]{exc}[/]")
            raise typer.Exit(code=2) from exc
```

- [ ] **Step 6.4: Thread selection into extraction and validate after**

In `src/kuroi/cli/run.py`, find the existing `extract_word_index` call (currently at line ~244):

```python
            try:
                result = extract_word_index(pdf)
            except OcrRequiredError as exc:
                ...
```

Replace with:

```python
            try:
                result = extract_word_index(pdf, selection=selection)
            except OcrRequiredError as exc:
                ...
```

(The `except OcrRequiredError` body is unchanged.)

Immediately after the OCR-applied notice line (currently at line ~256), insert the validation:

```python
            if selection is not None:
                try:
                    selection = validate_page_selection(
                        selection, page_count=result.total_pages
                    )
                except PageSelectionError as exc:
                    console.print(f"  [red]{exc}[/]")
                    raise typer.Exit(code=2) from exc
```

- [ ] **Step 6.5: Build `pages_by_number` and swap index lookups**

Immediately after the existing `pages = result.pages` assignment (currently line ~253), add:

```python
            pages_by_number = index_by_number(pages)
```

Replace the first index-based lookup in `_on_batch_complete` (currently around line 308):

```python
                    block_total = sum(
                        len({w.block_id for w in pages[pn - 1].words})
                        for pn in summary.page_numbers
                    )
```

With:

```python
                    block_total = sum(
                        len({w.block_id for w in pages_by_number[pn].words})
                        for pn in summary.page_numbers
                    )
```

Replace the second index-based lookup in the finding-audit loop (currently around line 406):

```python
                for f in findings:
                    page = pages[f.page - 1]
```

With:

```python
                for f in findings:
                    page = pages_by_number[f.page]
```

- [ ] **Step 6.6: Extend the pre-call cost line with the selection footer**

The current print sits at roughly line 263:

```python
            console.print(
                f"  Estimated cost: ${estimated_cost:.4f}  "
                f"({input_tokens} input tokens, {config.provider}/{config.model})"
            )
```

Replace with:

```python
            selection_note = (
                f", pages {selection.raw} of {result.total_pages}"
                if selection is not None
                else ""
            )
            console.print(
                f"  Estimated cost: ${estimated_cost:.4f}  "
                f"({input_tokens} input tokens, {config.provider}/{config.model}"
                f"{selection_note})"
            )
```

Note: the spec also describes a `→ N batches` suffix when batching is enabled. The existing code does *not* currently print a batch count on this line — batching shows up via the per-batch `Batch K/N` progress lines. Adding `→ N batches` to this string here would be a parallel cosmetic change that's out of scope for this plan. **Defer it.** The selection footer alone is the deliverable.

- [ ] **Step 6.7: Pass selection fields to `AuditLog.open`**

The current `AuditLog.open(...)` call sits at roughly line 372 inside `cli/run.py`. Find the existing kwargs:

```python
            audit = AuditLog.open(
                audit_path,
                original=pdf,
                output=final_output,
                provider=provider.name,
                model=provider.model,
                rules=tuple(rs.name for rs in rule_sets),
                session_id=session_id,
                input_sha256=input_sha256,
                input_pages=len(pages),
                input_bytes=len(input_bytes),
                model_version=getattr(provider, "model_version", provider.model),
                instructions=({"text": instruct},) if instruct else (),
                config_resolved_from=(),
            )
```

Add two trailing kwargs:

```python
                pages_spec=selection.raw if selection is not None else None,
                pages_resolved=selection.pages if selection is not None else (),
```

Note that `input_pages=len(pages)` already reflects the selected page count (because `pages` is now the selected subset). The audit's `input_pages` therefore equals the number of pages the pipeline actually processed; the original document size is recoverable from `pages_resolved` length plus skipped pages — but more directly, the spec doesn't require recording original page count in the header. Leave `input_pages` as-is.

- [ ] **Step 6.8: Run the new and existing CLI tests**

Run: `pytest tests/cli/test_run.py -v`

Expected: All tests PASS, including the four new `--pages` tests added in step 6.1.

- [ ] **Step 6.9: Run the full test suite**

Run: `pytest -v`

Expected: All tests PASS. Pay attention to `tests/core/test_audit.py`, `tests/core/test_pdf.py`, `tests/cli/test_run.py`, and any audit-schema tests in `tests/docs/` — the conditional inclusion of `pages_spec`/`pages_resolved` was designed to keep those untouched, but a full pass confirms it.

- [ ] **Step 6.10: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(run): add --pages flag for restricting pipeline to a page subset"
```

---

## Task 7: Final lint, type-check, and smoke pass

**Files:** none (CI parity)

- [ ] **Step 7.1: Run ruff**

Run: `ruff check src tests`

Expected: clean. Fix any issues introduced.

- [ ] **Step 7.2: Run ruff format**

Run: `ruff format --check src tests`

Expected: clean. If files are reformatted, run `ruff format src tests` and amend the commits from earlier tasks where the affected file lives:

```bash
git add <file>
git commit -m "style: apply ruff format"
```

- [ ] **Step 7.3: Run mypy**

Run: `mypy src`

Expected: clean. Fix any type errors. The most likely place for an issue is the `PageSelection.__contains__` method, where `n: object` is required by Python's `Container` protocol but you may want to narrow it inside.

- [ ] **Step 7.4: Smoke test end-to-end**

Manual quick check (no commit — just verify visually):

```bash
python -c "
import pymupdf
doc = pymupdf.open()
for i in range(5):
    p = doc.new_page()
    p.insert_text((72, 72), f'page {i+1} alice@example.com', fontsize=11)
doc.save('/tmp/kuroi_smoke.pdf')
doc.close()
"

kuroi run /tmp/kuroi_smoke.pdf --rules pii --pages 1-3 -o /tmp/kuroi_smoke.out.pdf -y --backup-dir /tmp/kuroi_smoke_backups --audit-dir /tmp/kuroi_smoke_audit
```

Expected output should include `pages 1-3 of 5` in the cost line, and the resulting PDF should have 5 pages with redactions only on pages 1–3. Confirm:

```bash
python -c "
import pymupdf
doc = pymupdf.open('/tmp/kuroi_smoke.out.pdf')
for p in doc:
    print(p.number + 1, p.get_text().strip())
doc.close()
"
```

Expected: pages 1–3 show the leading "page N" text with the email redacted (or stripped); pages 4–5 still contain the full `alice@example.com`.

- [ ] **Step 7.5: Confirm no uncommitted changes**

Run: `git status`

Expected: clean working tree (or only the pre-existing `uv.lock` modification from before this plan started).

---

## Spec Coverage Self-Check

Spec sections → tasks that implement them:

- **CLI surface** (`--pages` option, help text, examples) → Task 6 (steps 6.3, 6.6 cost line).
- **Semantics** (zero processing on unselected, full-page output, original page numbers preserved, out-of-range = hard error) → Tasks 2 (validation), 4 (extraction gating), 6 (wiring); plus Task 6.1 tests `pages_processes_only_selected` and `rejects_out_of_range_pages`.
- **Architecture › new module `page_selection.py`** → Tasks 1, 2.
- **Parser rules** (single/list/range/mixed/whitespace/sort-dedupe/rejections) → Task 1.
- **Validation rules** (in-range pass, single/partial/range out-of-range fail) → Task 2.
- **`core/pdf.py` changes** (`selection` param, `total_pages`, `index_by_number`) → Tasks 3, 4.
- **`cli/run.py` changes** (parse, extract, validate, dict swap, cost line) → Task 6.
- **Batching interaction** (no code change in `core/chunking.py`; contiguous batching over selection) → verified by Task 6.1 test `pages_and_batch_chunks_selection_contiguously`.
- **Audit** (optional `pages_spec`, `pages_resolved`, absent when unset) → Task 5; CLI wiring in Task 6.7; verified by Task 6.1 test `pages_processes_only_selected`.
- **Out of scope** items (open ranges, `all` keyword, verify/undo, audit merging, token-budgeted selections) — not implemented, intentionally. ✓

No gaps.

## Placeholder Scan

- No "TBD" / "TODO" / "fill in" anywhere in tasks.
- Every code-change step shows the full code to write or the exact before-and-after replacement.
- Every test step contains the full test body inline.
- Every command step shows the exact `pytest` / `git` invocation.

## Type Consistency

- `PageSelection(raw: str, pages: tuple[int, ...])` — same signature in Tasks 1, 2, 4, 6.
- `PageSelectionError` — raised in Tasks 1, 2, caught in Task 6 (correct class).
- `parse(spec: str) -> PageSelection` — same signature everywhere; imported as `parse_page_selection` in `cli/run.py` to avoid name collision with the existing local namespace.
- `validate(selection, *, page_count)` — keyword-only `page_count`, consistent between Task 2 implementation and Task 6 call site.
- `extract_word_index(pdf_path, *, selection=None)` — kw-only `selection` everywhere.
- `ExtractionResult.total_pages` — added in Task 3, read in Task 6 (cost line and validation).
- `index_by_number` — added in Task 3, imported and called in Task 6.
- `AuditLog.open(..., pages_spec=None, pages_resolved=())` — same signature in Task 5 impl and Task 6 call site.
