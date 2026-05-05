# OCR Pre-processing for Scanned PDFs

**Date:** 2026-05-05
**Status:** Approved

## Overview

kuroi currently extracts text using PyMuPDF's text layer. PDFs that are scanned images with no embedded text layer produce zero words, so no redactions are proposed. This feature adds automatic OCR via PyMuPDF's tesseract bridge: image-only pages are detected and OCR'd transparently before the rest of the pipeline runs.

## Behaviour

- Pages with zero extracted words **and** at least one embedded image are treated as scan candidates.
- Pages with zero words and no images are treated as blank and left alone.
- If scan candidates are found and `tesseract` is in PATH, OCR is applied silently and a notice is printed: `OCR applied to N scanned page(s).`
- If scan candidates are found and `tesseract` is **not** in PATH, a hard error is raised, a friendly message is printed, and the process exits with code 2.
- No new CLI flags — detection and OCR are fully automatic.

## Architecture

### `core/pdf.py`

**New exception:**

```python
class OcrRequiredError(Exception):
    def __init__(self, page_numbers: tuple[int, ...]) -> None:
        self.page_numbers = page_numbers
```

Raised when image pages are detected and `tesseract` is not in PATH. Carries affected page numbers for error reporting.

**New return type:**

```python
@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[Page, ...]
    ocr_page_count: int  # 0 if no OCR was needed
```

**Updated `extract_word_index(pdf_path: Path) -> ExtractionResult`:**

1. First pass — for each page, attempt `get_text("words")`. Collect words and record which pages returned zero words with at least one image (`page.get_images()` non-empty) as scan candidates.
2. If any scan candidates exist and `shutil.which("tesseract")` is `None` → raise `OcrRequiredError(tuple(scan_page_numbers))` immediately, before any OCR is attempted.
3. Second pass (scan candidates only) — call `page.get_textpage_ocr(full=True, language="eng", dpi=300)`, extract words via `page.get_text("words", textpage=tp)`. Replace the empty word list for that page. Word construction is identical to the non-OCR path.
4. Return `ExtractionResult(pages=tuple(pages), ocr_page_count=len(scan_candidates))`.

**OCR call detail:** `page.get_textpage_ocr()` is PyMuPDF's built-in tesseract bridge (available since PyMuPDF ≥ 1.18; project requires ≥ 1.24). It spawns `tesseract` as a subprocess and returns a `TextPage` whose word list has the same `(x0, y0, x1, y1, word, ...)` tuple format as regular `get_text("words")`, so `Word` construction is unchanged.

### `cli/run.py`

Unpack the new return value:

```python
result = extract_word_index(pdf)
pages = result.pages
```

Catch `OcrRequiredError` alongside `ConfigError`:

```python
except OcrRequiredError as exc:
    pages_str = ", ".join(str(p) for p in exc.page_numbers)
    console.print(
        f"  [red]Scanned pages detected (pages {pages_str}) but tesseract is not installed.[/]\n"
        f"  Install tesseract and re-run, or run `kuroi doctor` for details."
    )
    raise typer.Exit(code=2) from exc
```

Print OCR notice (before cost estimate) when `result.ocr_page_count > 0`:

```python
if result.ocr_page_count > 0:
    console.print(f"  OCR applied to {result.ocr_page_count} scanned page(s).")
```

### `cli/doctor.py`

Update the tesseract check detail from `"optional for v0.1"` to `"required for scanned PDFs"`.

## Data flow

```
extract_word_index(pdf)
  ├─ page has words → use as-is
  ├─ page has 0 words + no images → treat as blank, 0 words
  └─ page has 0 words + has images → scan candidate
        ├─ tesseract in PATH → get_textpage_ocr() → words
        └─ tesseract not in PATH → raise OcrRequiredError

run.py
  ├─ OcrRequiredError → error message + exit 2
  ├─ ocr_page_count > 0 → print notice
  └─ continue pipeline unchanged (rules, LLM, redaction, audit)
```

## Testing

### `tests/core/test_pdf.py`

- **`test_extract_word_index_ocrs_image_pages`**: build an in-memory PDF with one image-only page (pixmap with no text rendered onto it), monkeypatch `shutil.which` to return a fake tesseract path, monkeypatch `page.get_textpage_ocr` to return a stub `TextPage` that yields known words. Assert `ocr_page_count == 1` and words match stub output.
- **`test_extract_word_index_raises_ocr_required_when_tesseract_missing`**: same image-only PDF, monkeypatch `shutil.which` to return `None`. Assert `OcrRequiredError` is raised with correct page numbers.
- **`test_extract_word_index_skips_blank_pages`**: PDF with a page that has zero words and zero images. Assert `ocr_page_count == 0` and no OCR is attempted.

### `tests/cli/test_run.py`

- **`test_run_prints_ocr_notice_when_scanned_pages_found`**: monkeypatch `extract_word_index` to return `ExtractionResult` with `ocr_page_count=2`. Assert `"OCR applied to 2 scanned page(s)."` in stdout.
- **`test_run_exits_2_when_ocr_required_error`**: monkeypatch `extract_word_index` to raise `OcrRequiredError((3, 7))`. Assert exit code 2 and page numbers in error message.

### `tests/cli/test_doctor.py`

- Assert tesseract check detail string is `"required for scanned PDFs"` (or absent/path if installed).

## Out of scope

- `--ocr` flag or any opt-out mechanism.
- Language selection (hardcoded `"eng"`).
- DPI configuration (hardcoded `300`).
- OCR result caching.
