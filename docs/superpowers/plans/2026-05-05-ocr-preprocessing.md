# OCR Pre-processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically OCR image-only PDF pages via PyMuPDF's tesseract bridge so that scanned PDFs produce redaction candidates instead of zero words.

**Architecture:** `extract_word_index` gains a two-pass approach — first pass collects words and flags pages with zero words + at least one image as scan candidates; if any candidates exist, tesseract presence is checked (error if absent), then a second pass OCRs each candidate in-place. The function return type changes from `tuple[Page, ...]` to the new `ExtractionResult` dataclass, which the CLI unpacks, printing an OCR notice when relevant.

**Tech Stack:** Python 3.12, PyMuPDF ≥ 1.24 (`page.get_textpage_ocr`), system `tesseract` binary, pytest + monkeypatch.

---

## File Map

| File | Change |
|------|--------|
| `src/kuroi/core/pdf.py` | Add `OcrRequiredError`, `ExtractionResult`, `_ocr_page_words`; rewrite `extract_word_index` |
| `src/kuroi/cli/run.py` | Unpack `ExtractionResult`, print OCR notice, catch `OcrRequiredError` → exit 2 |
| `src/kuroi/cli/doctor.py` | Update tesseract-missing detail string |
| `tests/core/test_pdf.py` | Update existing tests; add 3 new OCR tests |
| `tests/cli/test_run.py` | Add 2 new CLI tests for OCR notice and error path |
| `tests/cli/test_doctor.py` | Add 1 new test for tesseract detail string |

---

### Task 1: Add `ExtractionResult` and update `extract_word_index` return type

This task changes the return type from `tuple[Page, ...]` to `ExtractionResult` and fixes every call site. No scan detection or OCR logic yet — `ocr_page_count` is always 0.

**Files:**
- Modify: `src/kuroi/core/pdf.py`
- Modify: `tests/core/test_pdf.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_pdf.py`:

```python
from kuroi.core.pdf import ExtractionResult, extract_word_index, serialize_for_llm


def test_extract_word_index_returns_extraction_result(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world"])

    result = extract_word_index(pdf)

    assert isinstance(result, ExtractionResult)
    assert result.ocr_page_count == 0
    assert len(result.pages) == 1
    assert result.pages[0].number == 1
    assert result.pages[0].words[0].text == "Hello"
    assert result.pages[0].words[1].text == "world"
```

Also update the two existing tests to use `.pages` (they break when the return type changes):

```python
def test_extract_word_index_returns_words_with_bboxes(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world"])

    result = extract_word_index(pdf)
    pages = result.pages  # ← was: pages = extract_word_index(pdf)

    assert len(pages) == 1
    page = pages[0]
    assert page.number == 1
    assert len(page.words) == 2
    assert page.words[0].text == "Hello"
    assert page.words[0].idx == 0
    assert page.words[1].text == "world"
    assert page.words[1].idx == 1
    for w in page.words:
        x0, y0, x1, y1 = w.bbox
        assert x1 > x0 and y1 > y0


def test_serialize_for_llm_emits_numbered_tokens(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world", "Second page"])
    result = extract_word_index(pdf)  # ← was: pages = extract_word_index(pdf)
    pages = result.pages              # ← new line

    text = serialize_for_llm(pages)

    assert '<page n="1">' in text
    assert '<page n="2">' in text
    assert "[0]Hello" in text
    assert "[1]world" in text
    assert "[0]Second" in text
    assert "[1]page" in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/core/test_pdf.py -v
```

Expected: `test_extract_word_index_returns_extraction_result` fails with `AttributeError: 'tuple' object has no attribute 'pages'`. The two updated existing tests also fail the same way.

- [ ] **Step 3: Add `ExtractionResult` and update `extract_word_index` in `src/kuroi/core/pdf.py`**

Replace the entire file content:

```python
"""PDF text extraction in the word-index protocol used throughout kuroi.

The protocol: every word on every page is assigned a stable per-page index. The
LLM sees `[idx]token` markers and returns `(page, start, end)` spans, which we
map back to bounding-box unions for redaction.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class Word:
    idx: int
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points


@dataclass(frozen=True)
class Page:
    number: int  # 1-indexed
    words: tuple[Word, ...]


class OcrRequiredError(Exception):
    def __init__(self, page_numbers: tuple[int, ...]) -> None:
        self.page_numbers = page_numbers


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[Page, ...]
    ocr_page_count: int  # 0 if no OCR was needed


def _ocr_page_words(page: pymupdf.Page) -> list:  # type: ignore[type-arg]
    tp = page.get_textpage_ocr(full=True, language="eng", dpi=300)  # type: ignore[no-untyped-call]
    return page.get_text("words", textpage=tp)  # type: ignore[no-untyped-call]


def extract_word_index(pdf_path: Path) -> ExtractionResult:
    """Extract every word on every page with its bounding box.

    Pages with zero words and at least one embedded image are scan candidates.
    If any scan candidates are found and tesseract is not in PATH, raises
    OcrRequiredError. Otherwise OCRs scan candidates via PyMuPDF's tesseract
    bridge. Returns an ExtractionResult whose ocr_page_count reflects how many
    pages were OCR'd (0 if none).
    """
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    try:
        pages: list[Page] = []
        scan_candidates: list[int] = []  # 1-indexed page numbers

        for page_idx in range(doc.page_count):
            pdf_page = doc[page_idx]
            raw = pdf_page.get_text("words")  # type: ignore[no-untyped-call]
            words = tuple(
                Word(
                    idx=i,
                    text=str(w[4]),
                    bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
                )
                for i, w in enumerate(raw)
            )
            pages.append(Page(number=page_idx + 1, words=words))
            if not words and pdf_page.get_images():  # type: ignore[no-untyped-call]
                scan_candidates.append(page_idx + 1)

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
                    )
                    for i, w in enumerate(raw)
                )
                pages[page_1idx - 1] = Page(number=page_1idx, words=words)

        return ExtractionResult(pages=tuple(pages), ocr_page_count=len(scan_candidates))
    finally:
        doc.close()  # type: ignore[no-untyped-call]


def serialize_for_llm(pages: tuple[Page, ...]) -> str:
    """Render the word index as the prompt-side representation.

    Output shape:
        <page n="1">
        [0]Hello [1]world
        </page>
        <page n="2">
        [0]Second [1]page
        </page>
    """
    chunks: list[str] = []
    for page in pages:
        body = " ".join(f"[{w.idx}]{w.text}" for w in page.words)
        chunks.append(f'<page n="{page.number}">\n{body}\n</page>')
    return "\n".join(chunks)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/core/test_pdf.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass. `run.py` still calls `extract_word_index(pdf)` and assigns result to `pages`; this will break the CLI tests — investigate any failures and note them (they will be fixed in Task 4).

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/pdf.py tests/core/test_pdf.py
git commit -m "feat: add ExtractionResult return type and OCR infrastructure to extract_word_index"
```

---

### Task 2: Add scan-candidate and `OcrRequiredError` tests

Task 1 already implemented the scan detection and `OcrRequiredError` logic. This task adds the tests that verify it, plus the blank-page edge case.

**Files:**
- Modify: `tests/core/test_pdf.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_pdf.py`. The full file now needs these imports at the top:

```python
import pymupdf
import pytest
```

Add after the existing tests:

```python
def _make_image_only_pdf(tmp_path: Path) -> Path:
    """Build a one-page PDF containing only a pixmap (no text layer)."""
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 128, 128))
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_extract_word_index_raises_ocr_required_when_tesseract_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _make_image_only_pdf(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(OcrRequiredError) as exc_info:
        extract_word_index(pdf)

    assert exc_info.value.page_numbers == (1,)


def test_extract_word_index_skips_blank_pages(make_pdf: Callable[..., Path]) -> None:
    # A page with zero words AND zero images is blank — not a scan candidate.
    pdf = make_pdf([""])  # insert_text with empty string → no words, no images

    result = extract_word_index(pdf)

    assert result.ocr_page_count == 0
    assert len(result.pages) == 1
    assert result.pages[0].words == ()
```

Update the import line at the top of `tests/core/test_pdf.py` to include `OcrRequiredError`:

```python
from kuroi.core.pdf import ExtractionResult, OcrRequiredError, extract_word_index, serialize_for_llm
```

- [ ] **Step 2: Run tests to verify they fail (or pass for newly-covered logic)**

```bash
uv run pytest tests/core/test_pdf.py::test_extract_word_index_raises_ocr_required_when_tesseract_missing tests/core/test_pdf.py::test_extract_word_index_skips_blank_pages -v
```

Since the logic was already written in Task 1, these tests should PASS. If either fails, the Task 1 implementation has a bug — fix it before proceeding.

- [ ] **Step 3: Run the full pdf test module**

```bash
uv run pytest tests/core/test_pdf.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/core/test_pdf.py
git commit -m "test: add scan-candidate detection and OcrRequiredError tests"
```

---

### Task 3: Add OCR second-pass test

Verifies that `_ocr_page_words` is called for scan candidates and that the returned words replace the empty word list.

**Files:**
- Modify: `tests/core/test_pdf.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_pdf.py`:

```python
def test_extract_word_index_ocrs_image_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = _make_image_only_pdf(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None)
    # Stub _ocr_page_words to return one known word without shelling out to tesseract.
    monkeypatch.setattr(
        "kuroi.core.pdf._ocr_page_words",
        lambda page: [(10.0, 20.0, 50.0, 30.0, "redacted", 0, 0, 0)],
    )

    result = extract_word_index(pdf)

    assert result.ocr_page_count == 1
    assert len(result.pages) == 1
    assert result.pages[0].words[0].text == "redacted"
    assert result.pages[0].words[0].idx == 0
    assert result.pages[0].words[0].bbox == (10.0, 20.0, 50.0, 30.0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/core/test_pdf.py::test_extract_word_index_ocrs_image_pages -v
```

Expected: FAIL — either `shutil.which` monkeypatching doesn't reach the right call site, or `_ocr_page_words` is actually called and shells out to a real tesseract that isn't installed (TypeError / FileNotFoundError). If the test PASSES immediately, the stub is working — proceed.

- [ ] **Step 3: Verify the monkeypatch target**

The monkeypatch `"kuroi.core.pdf._ocr_page_words"` targets the function object in `pdf.py`. The `shutil.which` monkeypatch `"shutil.which"` patches the function on the `shutil` module object, which `pdf.py` accesses as `shutil.which(...)` after `import shutil`. Both should work. If the test fails with a real tesseract call, double-check that `pdf.py` imports `shutil` at the top (not `from shutil import which`) — the monkeypatch requires `shutil.which` to be looked up at call time.

- [ ] **Step 4: Run the full pdf test module**

```bash
uv run pytest tests/core/test_pdf.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/core/test_pdf.py
git commit -m "test: add OCR second-pass test with stubbed _ocr_page_words"
```

---

### Task 4: Update `cli/run.py` to unpack `ExtractionResult` and handle `OcrRequiredError`

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Write the failing CLI tests**

Add to `tests/cli/test_run.py` (after the existing imports; `runner` is already defined at module level):

```python
def test_run_prints_ocr_notice_when_scanned_pages_found(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic_client: dict[str, Any],
) -> None:
    from kuroi.core.pdf import ExtractionResult, Page

    pdf = make_pdf(["Hello world"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        "kuroi.cli.run.extract_word_index",
        lambda path: ExtractionResult(pages=(Page(number=1, words=()),), ocr_page_count=2),
    )

    result = runner.invoke(app, ["run", str(pdf), "--instruct", "redact all", "-y"])

    assert "OCR applied to 2 scanned page(s)." in result.stdout


def test_run_exits_2_when_ocr_required_error(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic_client: dict[str, Any],
) -> None:
    from kuroi.core.pdf import OcrRequiredError

    pdf = make_pdf(["Hello world"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def _raise(path: Path) -> None:
        raise OcrRequiredError((3, 7))

    monkeypatch.setattr("kuroi.cli.run.extract_word_index", _raise)

    result = runner.invoke(app, ["run", str(pdf), "--instruct", "redact all", "-y"])

    assert result.exit_code == 2
    assert "3, 7" in result.stdout
    assert "tesseract" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/cli/test_run.py::test_run_prints_ocr_notice_when_scanned_pages_found tests/cli/test_run.py::test_run_exits_2_when_ocr_required_error -v
```

Expected: both FAIL — `run.py` currently assigns `extract_word_index(pdf)` directly to `pages`, so `pages.pages` would blow up, or `OcrRequiredError` propagates uncaught.

- [ ] **Step 3: Update `src/kuroi/cli/run.py`**

Change the import line:

```python
from kuroi.core.pdf import extract_word_index, serialize_for_llm
```

to:

```python
from kuroi.core.pdf import OcrRequiredError, extract_word_index, serialize_for_llm
```

Replace these lines inside the `with output_lock(final_output):` block (around line 125 currently):

```python
            pages = extract_word_index(pdf)
```

with:

```python
            try:
                result = extract_word_index(pdf)
            except OcrRequiredError as exc:
                pages_str = ", ".join(str(p) for p in exc.page_numbers)
                console.print(
                    f"  [red]Scanned pages detected (pages {pages_str}) but tesseract is not installed.[/]\n"
                    f"  Install tesseract and re-run, or run `kuroi doctor` for details."
                )
                raise typer.Exit(code=2) from exc

            pages = result.pages

            if result.ocr_page_count > 0:
                console.print(f"  OCR applied to {result.ocr_page_count} scanned page(s).")
```

The OCR notice is printed before the cost estimate (the `console.print` for estimated cost follows a few lines later — leave it in place).

- [ ] **Step 4: Run the two new tests**

```bash
uv run pytest tests/cli/test_run.py::test_run_prints_ocr_notice_when_scanned_pages_found tests/cli/test_run.py::test_run_exits_2_when_ocr_required_error -v
```

Expected: both PASS.

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass. If any existing `test_run.py` tests fail, they likely pass `pages` (tuple) somewhere that now expects `ExtractionResult` — check that `run.py` uses `result.pages` consistently.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat: unpack ExtractionResult in run.py; print OCR notice; catch OcrRequiredError"
```

---

### Task 5: Update `cli/doctor.py` tesseract detail string

**Files:**
- Modify: `src/kuroi/cli/doctor.py`
- Modify: `tests/cli/test_doctor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_doctor.py`:

```python
def test_doctor_tesseract_detail_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None if name == "tesseract" else f"/usr/bin/{name}")
    result = CliRunner().invoke(app, ["doctor"])
    assert "required for scanned PDFs" in result.stdout
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/cli/test_doctor.py::test_doctor_tesseract_detail_when_missing -v
```

Expected: FAIL — the current detail string is `"optional for v0.1"`.

- [ ] **Step 3: Update `src/kuroi/cli/doctor.py`**

Change `_binary_check` to accept an optional `detail_missing` parameter:

```python
def _binary_check(name: str, detail_missing: str | None = None) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, "ok", path)
    missing = detail_missing if detail_missing is not None else f"{name} not found in PATH"
    return CheckResult(name, "warn", missing)
```

Update the `doctor()` callback to pass a custom detail for tesseract:

```python
    checks: list[CheckResult] = [
        CheckResult(f"kuroi version {__version__}", "ok", ""),
        _python_version(),
        _anthropic_key(),
        _binary_check("tesseract", detail_missing="required for scanned PDFs"),
        _binary_check("qpdf"),
        *_config_checks(),
    ]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/cli/test_doctor.py -v
```

Expected: all doctor tests PASS including the new one.

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest --tb=short -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/doctor.py tests/cli/test_doctor.py
git commit -m "feat: update tesseract doctor detail to 'required for scanned PDFs'"
```
