# Page Selection for `kuroi run`

**Date:** 2026-05-11
**Status:** Draft

## Overview

Add a `--pages` option to `kuroi run` that restricts the entire processing
pipeline to a user-specified subset of pages. Selected pages are extracted,
OCR-checked, regex-scanned, and sent to the LLM as usual. Unselected pages
receive zero processing and pass through the output PDF unchanged.

The flag is opt-in. Omitting `--pages` preserves the current behavior of
processing every page.

## Motivation

A user working on a long document often only needs to redact a known subset
of pages — a single exhibit inside a court filing, a specific section of a
report, or the first few pages of a scan. Today `kuroi run` always processes
the whole document, which:

- Incurs LLM cost on pages the user knows are irrelevant.
- Forces an OCR install even when the only image-only pages sit outside the
  user's area of interest.
- Makes iteration on a problematic page slower than it needs to be (every
  re-run reprocesses everything).

The fix is a single new flag whose semantics match the user's mental model:
"work on these pages, leave the rest alone."

## CLI surface

One new option on `kuroi run`:

```
--pages SPEC     Process only the listed pages. SPEC is a comma-separated
                 list of page numbers (`1,3,10`) and/or inclusive ranges
                 (`1-5`). Mixed forms are allowed (`1,3-5,10`). Whitespace
                 is tolerated. Default: all pages.
```

Examples:

- `kuroi run doc.pdf --pages 1`
- `kuroi run doc.pdf --pages 1,2,14`
- `kuroi run doc.pdf --pages 1-5`
- `kuroi run doc.pdf --pages 10-25`
- `kuroi run doc.pdf --pages "1, 3-5, 10"`

The pre-call cost line gains a selection footer when `--pages` is in effect:

```
Estimated cost: $0.0008  (3142 input tokens, anthropic/claude-opus-4-7, pages 1-5,10 of 49)
```

Combined with batching:

```
Estimated cost: $0.0008  (3142 input tokens, anthropic/claude-opus-4-7, pages 1-5,10 of 49 → 2 batches)
```

## Semantics

`--pages X` means: do all of {extract, OCR-check, regex, LLM, redaction} on
the selected pages only. Unselected pages are not extracted, not OCR'd, not
scanned by regex, not sent to the LLM, and not modified in the output PDF.

The output PDF retains the full page count of the input. Page numbers are
preserved everywhere (audit, findings, batch records) — a selection of
`1-5,10` reports findings on pages 1–5 and 10 with those original numbers.

Out-of-range pages are a hard error (exit code 2). A typo that produces zero
coverage must never silently widen or narrow the redacted area.

## Architecture

### New module `src/kuroi/core/page_selection.py`

```python
@dataclass(frozen=True)
class PageSelection:
    raw: str                      # original flag value, for audit
    pages: tuple[int, ...]        # sorted, deduped, 1-indexed

    def __contains__(self, n: int) -> bool: ...
    def __iter__(self) -> Iterator[int]: ...
    def __len__(self) -> int: ...


class PageSelectionError(ValueError):
    """Raised on malformed --pages input or out-of-range pages."""


def parse(spec: str) -> PageSelection:
    """Parse `--pages` syntax. Does NOT validate against a document.

    Grammar: SPEC := PART ("," PART)*
             PART := INT | INT "-" INT
    Whitespace ignored. Empty SPEC raises.
    """


def validate(selection: PageSelection, page_count: int) -> PageSelection:
    """Ensure every page is in [1, page_count]. Raise PageSelectionError otherwise.

    Returns the same selection for chaining (no transformation — out-of-range
    is a hard error per the design).
    """
```

#### Parser rules

| Input | Outcome |
|---|---|
| `"1"` | `(1,)` |
| `"1,2,14"` | `(1, 2, 14)` |
| `"1-5"` | `(1, 2, 3, 4, 5)` |
| `"3,1,2,3"` | `(1, 2, 3)` — sorted, deduped |
| `"1,3-5,10"` | `(1, 3, 4, 5, 10)` |
| `"  1 , 3 - 5 "` | `(1, 3, 4, 5)` — whitespace OK |
| `""` | `PageSelectionError("empty page selection")` |
| `"0"`, `"-3"` | `PageSelectionError("page numbers must be >= 1")` |
| `"5-1"` | `PageSelectionError("range 5-1 is reversed")` |
| `"1-"`, `"-5"` | `PageSelectionError("open ranges not supported")` |
| `"1,abc"` | `PageSelectionError("not a number: 'abc'")` |

#### Validation rules

| Input | Doc pages | Outcome |
|---|---|---|
| `"99"` | 10 | `PageSelectionError("page(s) 99 not in document (1-10)")` |
| `"1-5,99"` | 10 | `PageSelectionError("page(s) 99 not in document (1-10)")` |
| `"1-15"` | 10 | `PageSelectionError("page(s) 11-15 not in document (1-10)")` |

In `cli/run.py`, both errors map to `typer.Exit(code=2)` printed via
`console.print(f"  [red]{exc}[/]")`, matching the existing handling for
`ConfigError` and `OutputResolutionError`.

### `core/pdf.py` changes

`extract_word_index` gains a keyword-only `selection` parameter:

```python
def extract_word_index(
    pdf_path: Path,
    *,
    selection: PageSelection | None = None,
) -> ExtractionResult:
    """Extract every word on every (selected) page with its bounding box.

    When `selection` is given, only the listed page numbers are extracted;
    OCR scan-candidate detection runs only for those pages. Returns a Page
    tuple containing only selected pages (preserving their original 1-indexed
    `number`).
    """
```

Implementation diff inside the function:

```python
for page_idx in range(doc.page_count):
    page_num = page_idx + 1
    if selection is not None and page_num not in selection:
        continue                       # skip extraction entirely
    pdf_page = doc[page_idx]
    raw = pdf_page.get_text("words")
    ...
    pages.append(Page(number=page_num, words=words))
```

The same `continue` short-circuit gates scan-candidate detection, so
`OcrRequiredError` only fires if a *selected* page needs OCR but tesseract
is missing.

`ExtractionResult` gains a `total_pages` field:

```python
@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[Page, ...]
    ocr_page_count: int
    total_pages: int       # NEW — doc.page_count, regardless of selection
```

This is needed because `cli/run.py` validates the selection against the
total page count *after* extraction has opened the document, and we don't
want a second `pymupdf.open(...)` just to read the page count.

A new helper in the same module:

```python
def index_by_number(pages: tuple[Page, ...]) -> dict[int, Page]:
    """Map 1-indexed page number → Page. Used wherever callers previously
    relied on the tuple being 1-indexed-dense (no longer true under --pages).
    """
    return {p.number: p for p in pages}
```

### `cli/run.py` changes

Add the new option (placed next to `--pages-per-batch` for discoverability):

```python
pages_spec: str | None = typer.Option(
    None,
    "--pages",
    help=(
        "Process only the listed pages. Comma-separated list of page "
        "numbers and/or inclusive ranges (e.g. `1`, `1,2,14`, `1-5`, "
        "`1,3-5,10`). Default: all pages."
    ),
),
```

Flow:

1. **Parse selection (before any I/O)** — fail fast on bad syntax:

   ```python
   selection: PageSelection | None = None
   if pages_spec is not None:
       try:
           selection = page_selection.parse(pages_spec)
       except PageSelectionError as exc:
           console.print(f"  [red]{exc}[/]")
           raise typer.Exit(code=2) from exc
   ```

2. **Extract with selection** — replaces the existing `extract_word_index` call:

   ```python
   try:
       result = extract_word_index(pdf, selection=selection)
   except OcrRequiredError as exc:
       ...  # unchanged; only fires for selected pages now
   ```

3. **Validate against page count** (after extraction succeeds):

   ```python
   if selection is not None:
       try:
           selection = page_selection.validate(selection, result.total_pages)
       except PageSelectionError as exc:
           console.print(f"  [red]{exc}[/]")
           raise typer.Exit(code=2) from exc
   ```

4. **Dict-lookup swap** — build once after extraction:

   ```python
   pages_by_number = index_by_number(pages)
   ```

   Replace `pages[pn - 1]` (the `_on_batch_complete` block computing
   layout-aware block totals, currently near line 298) with
   `pages_by_number[pn]`. Replace `pages[f.page - 1]` (the finding-audit
   loop, currently near line 407) with `pages_by_number[f.page]`. These are
   the only two index-based lookups; `redaction.py:28` already builds its
   own page-number dict.

5. **Cost line footer** — extend the existing print:

   ```python
   selection_note = ""
   if selection is not None:
       selection_note = f", pages {selection.raw} of {result.total_pages}"
   batches_note = f" → {total_batches} batches" if batched_ui else ""
   console.print(
       f"  Estimated cost: ${estimated_cost:.4f}  "
       f"({input_tokens} input tokens, {config.provider}/{config.model}"
       f"{selection_note}{batches_note})"
   )
   ```

6. **No change to redaction call.** `apply_redactions(pdf, findings, pages, temp_out)`
   already iterates `doc.page_count` internally and gates on
   `findings_by_page.get(page_num, [])`. With a partial `pages` tuple and
   findings only for selected pages, unselected pages hit the existing
   `continue` branch and pass through untouched.

### Batching interaction

No code change in `core/chunking.py`. `detect_redactions_chunked` slices
whatever `pages` tuple it receives, so
`--pages 1-5,10,15-20 --pages-per-batch 3` naturally produces:

```
[1, 2, 3]  →  [4, 5, 10]  →  [15, 16, 17]  →  [18, 19, 20]
```

Contiguous batches over the selection. Page numbers stay original
(1-indexed against the source doc), and `chunk.pages` in audit reflects
exactly what the model saw.

## Audit

`AuditLog.open()` in `core/audit.py` gains two new header fields, both
optional and both `None`/`()` by default:

```python
pages_spec: str | None = None,            # raw flag value, e.g. "1-5,10"
pages_resolved: tuple[int, ...] = (),     # sorted resolved set, e.g. (1,2,3,4,5,10)
```

`cli/run.py` passes them in the existing `AuditLog.open(...)` call:

```python
pages_spec=selection.raw if selection else None,
pages_resolved=selection.pages if selection else (),
```

When `--pages` is not given, both fields are absent from the JSONL record —
no schema change visible to existing audit consumers. Per-batch
`chunk_request` events keep their existing `pages` field unchanged; they
naturally only list selected pages because that's what the chunking
orchestrator received.

## Tests

New `tests/core/test_page_selection.py`:

1. `test_parse_single` — `"5"` → `(5,)`.
2. `test_parse_list` — `"1,2,14"` → `(1, 2, 14)`.
3. `test_parse_range` — `"1-5"` → `(1, 2, 3, 4, 5)`.
4. `test_parse_mixed` — `"1,3-5,10"` → `(1, 3, 4, 5, 10)`.
5. `test_parse_whitespace_tolerant` — `"  1 , 3 - 5 "` → `(1, 3, 4, 5)`.
6. `test_parse_sorts_and_dedupes` — `"3,1,2,3"` → `(1, 2, 3)`.
7. `test_parse_rejects_empty` — `""` raises `PageSelectionError`.
8. `test_parse_rejects_zero_and_negative` — `"0"`, `"-3"` raise.
9. `test_parse_rejects_reversed_range` — `"5-1"` raises.
10. `test_parse_rejects_open_range` — `"1-"`, `"-5"` raise.
11. `test_parse_rejects_non_numeric` — `"1,abc"` raises.
12. `test_validate_passes_when_in_range` — selection `(1,5,10)` against
    10-page doc is fine.
13. `test_validate_rejects_out_of_range_single` — `(99,)` against 10-page
    doc raises with message naming page 99.
14. `test_validate_rejects_out_of_range_partial` — `(1,2,99)` against
    10-page doc raises (no silent trim).

New `tests/core/test_pdf_selection.py`:

15. `test_extract_word_index_filters_to_selection` — 10-page fixture PDF
    with `selection={2,5}` returns 2 pages with `.number` 2 and 5.
16. `test_extract_word_index_skips_ocr_for_unselected` — fixture where
    page 1 is text and page 2 is image-only; `selection={1}` returns 1
    page and does NOT raise `OcrRequiredError` even with
    `shutil.which("tesseract")` patched to `None`.
17. `test_extract_word_index_reports_total_pages` —
    `ExtractionResult.total_pages == doc.page_count` regardless of
    selection size.

Add to `tests/cli/test_run.py`:

18. `test_run_with_pages_processes_only_selected` — 4-page fake-provider
    fixture; `--pages 1-2` produces findings only for pages 1–2;
    redaction output retains all 4 pages; audit header records
    `pages_spec="1-2"` and `pages_resolved=[1,2]`.
19. `test_run_with_pages_and_batch_chunks_selection_contiguously` —
    20-page fake doc; `--pages "1-5,10,15-20" --pages-per-batch 3`
    produces 4 batches with the exact page groupings above.
20. `test_run_rejects_out_of_range_pages` — 3-page doc; `--pages 1,99`
    exits with code 2 and the error message references page 99.
21. `test_run_rejects_malformed_pages` — `--pages "abc"` exits with code 2.

## Out of scope

Explicitly deferred:

- **Open-ended ranges** (`-5`, `10-`). Rejected during brainstorming;
  easy to add later if asked for.
- **`all` keyword.** The default *is* "all pages" — `--pages all` adds
  no value.
- **Page selection in `kuroi verify` / `kuroi undo`.** These consume the
  audit log's resolved page set as-is; no separate flag is needed.
- **Re-running on different page subsets and merging audits.** Each run
  is independent; merging is a separate concern.
- **Token-budgeted selections** (e.g. "pick pages until N tokens"). Out
  of scope; this spec is about explicit user selection.
