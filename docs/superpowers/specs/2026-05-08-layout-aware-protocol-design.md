# Layout-Aware Word-Index Protocol

**Date:** 2026-05-08
**Status:** Draft

## Overview

Wrap kuroi's existing flat `[idx]token` prompt stream with PyMuPDF block
boundaries so the LLM sees which runs of words belong to the same
paragraph, caption, header, or footer. The word index, response shape,
and bbox round-trip are unchanged — only the prompt's framing gains
structural metadata. Block IDs come from PyMuPDF's existing layout
analysis on the call kuroi already makes (`get_text("words")`); no new
extraction pass and no new dependencies.

This is a small opt-in change (one CLI flag, one TOML key, additive
prompt template) that establishes the protocol scaffolding. Two
follow-on specs slot into the same `<block>` envelope without further
protocol churn: a **table-aware overlay** for column-vs-name
disambiguation, and **cross-page recurring-element dedup** for the
biggest token-savings wins. Both are explicitly out of scope here —
deferred until telemetry from plain block tags tells us which is the
long pole on real documents.

The change is contained to `Word` (one new field) and `serialize_for_llm`
(one new branch) in `core/pdf.py`, a `layout_aware` keyword threaded
through the providers, one CLI flag in `cli/run.py`, and one config key
under `[prompt]`. The redaction, diff, verify, audit, and chunking
pipelines are unchanged. Subdivide-on-failure interacts cleanly: sliced
pages preserve block IDs, so subdivided batches emit consistent block
tags.

## Motivation

Two failure modes traceable in production runs today:

1. **Field-label vs field-value confusion.** The flat stream
   `[42]Patient [43]Name [44]: [45]John [46]Doe` gives the LLM no way to
   distinguish the structural label "Patient Name:" from the value
   "John Doe". The model sometimes redacts the label, the colon, or
   both. With a block boundary wrapping each ("Patient Name:" in one
   block, "John Doe" in another) the model sees they are independent
   units and learns to treat the value as the candidate.

2. **Cross-batch context loss after subdivision.** The
   `2026-05-08-subdivide-on-failure-design.md` work halves dense pages
   with a 50-word overlap window. When a paragraph straddles the cut,
   both halves see the boundary words but neither sees they belonged to
   one paragraph. Block tags tell the LLM "this run starts/ends
   mid-block, do not draw context conclusions across the cut" —
   restoring some of the context lost to subdivision.

A third failure mode (table-cell column confusion — column header that
looks like a name redacted as PII) is **not addressed by this spec**.
Block tags alone get you "this is paragraph A vs paragraph B"; they do
not yet get you "this is column 3 of a 5-column table". That requires
the table-aware overlay deferred to Future work.

## What PyMuPDF already gives us

`page.get_text("words")` — the call kuroi already makes in
`core/pdf.py:68` — returns 8-tuples shaped
`(x0, y0, x1, y1, text, block_no, line_no, word_no)`. The 6th element
is the block index PyMuPDF assigns during layout analysis. Today kuroi
reads the four bbox floats and the `text`; `block_no`, `line_no`, and
`word_no` are discarded.

This means **block boundaries can be added to the prompt with no second
extraction pass and no new dependencies.** PyMuPDF computes them on the
call already in flight.

## Architecture

### `Word` gains a `block_id`

`src/kuroi/core/pdf.py`:

```python
@dataclass(frozen=True)
class Word:
    idx: int
    text: str
    bbox: tuple[float, float, float, float]
    block_id: int  # NEW; PyMuPDF block_no
```

Both extraction paths (`extract_word_index` for native PDFs and
`_ocr_page_words` for OCR'd pages) currently slice `w[0..4]` from
PyMuPDF's tuple. Updated to also pull `w[5]` for `block_id`. The native
path produces meaningful block boundaries; the OCR path typically
produces one block for the whole page (PyMuPDF's `get_textpage_ocr`
returns OCR'd content as a single block group), which is the degenerate
case but harmless — the layout-aware prompt for an OCR'd page wraps the
whole page in one `<block>`.

`Page.slice_page` (added by the subdivide-on-failure design at
`core/pdf.py`) carries `block_id` forward alongside `text` and `bbox`.
A subdivided slice can therefore still emit consistent block tags; a
half whose boundary cuts mid-block emits a `<block>` that opens or
closes mid-content, which is exactly the signal the LLM needs to avoid
drawing context conclusions across the cut.

### `serialize_for_llm` emits block tags

`src/kuroi/core/pdf.py:102` gains a keyword-only `layout_aware: bool`
defaulting to `False`. When off: byte-identical to today. When on:

```python
def serialize_for_llm(
    pages: tuple[Page, ...],
    *,
    layout_aware: bool = False,
) -> str:
    chunks: list[str] = []
    for page in pages:
        if layout_aware:
            body = _serialize_blocks(page.words)
        else:
            body = " ".join(f"[{w.idx}]{w.text}" for w in page.words)
        chunks.append(f'<page n="{page.number}">\n{body}\n</page>')
    return "\n".join(chunks)


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
```

The result is in document order; consecutive words sharing a `block_id`
stay in one `<block>`. Blocks split across pages emit twice (once per
page) — there is no cross-page block continuity in PyMuPDF's model, and
that is fine: the LLM never operates on cross-page context anyway.

The `[N]` markers inside each `<block>` continue to be the global
per-page word index, so response indices map back to `Page.words[N]`
exactly as today. **Block tags are advisory metadata for the LLM, never
structural state in the response.**

### Provider plumbing

Both providers route through `providers/_shared.py`, which owns the
shared `SYSTEM_PROMPT` constant and the `build_user_prompt` helper that
calls `serialize_for_llm`. The provider plumbing change therefore
lands almost entirely in `_shared.py`:

1. `SYSTEM_PROMPT` becomes the *base*; a new
   `build_system_prompt(layout_aware: bool) -> str` returns
   `SYSTEM_PROMPT` unchanged when off, and `SYSTEM_PROMPT` plus the
   layout-aware paragraph when on. Existing callers that read
   `SYSTEM_PROMPT` directly are migrated to call `build_system_prompt`.
2. `build_user_prompt` gains a `layout_aware: bool = False` keyword and
   forwards it to `serialize_for_llm`.
3. The abstract `Provider.detect_redactions` in `providers/base.py`
   gains `layout_aware: bool = False` after `attempt`. Both
   `AnthropicProvider.detect_redactions` and
   `OllamaProvider.detect_redactions` accept it and pass it into
   `build_system_prompt` and `build_user_prompt`.

The layout-aware paragraph appended to the system prompt:

```
The page content is organized into <block id="N">...</block> sections
corresponding to layout-detected paragraphs and other typographic units.
Use the block boundaries as context to disambiguate which words refer to
the same entity (e.g. a field label vs its value), but report findings
exactly as before, using the per-page word indices [N], not block IDs.
Do not report block IDs in your response.
```

The response schema is unchanged. The LLM still returns
`{page, start, end, kind}` tuples; the orchestrator still maps
`start..end` back to `Page.words[start..end]` for bbox unions.

### Chunking orchestrator plumbing

`detect_redactions_chunked` in `core/chunking.py` gains
`layout_aware: bool = False` (keyword-only, after `retry_policy`) and
forwards it on every `provider.detect_redactions(...)` call inside the
batch loop. Default is off so existing callers (and the existing
non-chunked path in `cli/run.py`) are byte-identical until they opt in.

Subdivision recursion (the `_try_or_subdivide` helper) threads
`layout_aware` through unchanged — sliced pages serialize with their
inherited block IDs, and the system-prompt paragraph is identical at
every recursion level.

### CLI surface

One new flag on `kuroi run`:

```
--layout-aware / --no-layout-aware  Wrap the LLM prompt with PyMuPDF
                                    block tags so the model can see
                                    paragraph and other layout
                                    boundaries. Default: off.
```

Wired through `cli/run.py` to both the unchunked direct provider call
and the chunked orchestrator call.

### Config

Optional `[prompt]` table in `kuroi.toml`:

```toml
[prompt]
layout_aware = true
```

Resolution order is the existing CLI > config > hardcoded-default
ladder used by retry policy and pages-per-batch:

| CLI flag | Config | Effective |
|---|---|---|
| `--layout-aware` | any | on |
| `--no-layout-aware` | any | off |
| (unset) | `true` | on |
| (unset) | `false` or absent | off |

## Failure modes

### Bad blocks from PyMuPDF

PyMuPDF's block detection is heuristic. Multi-column pages may produce
blocks that span columns; tables may collapse into one block; poorly
tagged PDFs may produce one block per page (no useful structure). In
each case:

- The LLM sees fewer block boundaries than ideal.
- The prompt is **not worse** than the flagless version — the words
  still appear in document order, the indices are still correct, and
  the system-prompt paragraph just has less to grip on.
- Bbox round-trip is unchanged; redaction continues to operate on word
  indices.

The worst case for block tags is "no benefit, slight token overhead."
There is no failure mode where block tags are actively wrong, because
they are advisory only.

### OCR'd pages

OCR-produced words typically share a single `block_id`. With
`layout_aware=True`, the prompt for an OCR'd page wraps the entire page
content in one `<block>` — equivalent to the flagless prompt with one
extra wrapper. Acceptable.

If a future PyMuPDF version returns per-paragraph blocks for OCR'd
content, this design picks up the improvement automatically.

### Token overhead

A page with 500 words distributed across 30 blocks adds roughly
30 × 14 chars (`<block id="NN">…</block>` framing) ≈ 420 chars ≈ 100
tokens of overhead. On a 2000-token page, that is a 4–5% bloat. The
expected accuracy gain is the trade.

A telemetry hook (logged at `INFO`) records the per-batch block count
when `layout_aware=True`, so a `-v` run shows
`… blocks=12 tokens_in=2871 tokens_out=412` and users can see the cost
on their own documents.

### Subdivide-on-failure interaction

A subdivided slice covering words `start..end` with mid-block
boundaries emits `<block>` tags that open or close partway through a
paragraph. The LLM is told (system-prompt paragraph) to use blocks as
boundary signals — a half-open block at the end of a slice means
"context cuts off here," which is precisely the signal we want. No
special-casing needed in `core/chunking.py`.

## Determinism

`block_id` from PyMuPDF is stable for a given document on a given
PyMuPDF version. With `temperature=0` and a fixed seed, two runs with
`--layout-aware` produce byte-identical prompts and therefore
byte-identical responses. Across PyMuPDF version upgrades, block
numbering may drift; this is the same versioning concern that already
applies to bbox computation and is documented in the existing
reproducibility guide.

## Audit

`ChunkRecord.prompt_sha256` covers any prompt change automatically. No
schema additions. Two runs with the flag on and off produce different
`prompt_sha256` values; the audit log already lets reviewers
reconstruct which form was used.

A small addition: when `layout_aware=True`, the `cli/run.py`
`on_batch_complete` closure logs the per-batch block count at `INFO`
alongside the existing duration and token counters.

## Tests

### `tests/core/test_pdf.py` *(existing file; new tests added)*

1. `test_word_carries_block_id_from_pymupdf` — extract a known fixture;
   assert each `Word` has a `block_id` matching the 6th element of
   PyMuPDF's tuple for that word.
2. `test_serialize_for_llm_default_unchanged` — assert
   `serialize_for_llm(pages)` (flag off) is byte-identical to the
   pre-change output. Regression-locks the no-flag path.
3. `test_serialize_for_llm_layout_aware_wraps_blocks` — page with three
   blocks (e.g. heading / paragraph / footer); assert three
   `<block id="N">…</block>` sections in document order with `[idx]token`
   markers preserved inside.
4. `test_serialize_for_llm_layout_aware_collapses_consecutive_same_block`
   — adjacent words sharing `block_id` are wrapped in a single `<block>`;
   non-adjacency by `block_id` flushes a new tag.
5. `test_slice_page_preserves_block_id` — sliced page (subdivide-on-
   failure path) carries forward block IDs from the parent; layout-aware
   serialization on a slice emits the parent's block IDs unchanged.
6. `test_ocr_page_layout_aware_emits_single_block` — OCR'd-page fixture;
   layout-aware serialization wraps the whole page in one `<block>`.

### `tests/providers/test_anthropic.py` and `test_ollama.py`

7. `test_layout_aware_propagates_through_provider` — patch
   `serialize_for_llm`; assert it is called with `layout_aware=True`
   when the provider receives that flag, and `False` otherwise.
8. `test_layout_aware_appends_system_prompt_paragraph` — assert the
   constructed system prompt contains the layout-aware explanation
   paragraph when the flag is on, and does not when it's off.
9. `test_response_parsing_unchanged_under_layout_aware` — provider
   round-trip with a stub LLM returning the canonical `{page, start, end}`
   schema; assert findings come back identically whether
   `layout_aware` was on or off.

### `tests/cli/test_run.py`

10. `test_run_with_layout_aware_flag_propagates` — `--layout-aware` on
    the CLI threads through to the provider call.
11. `test_run_layout_aware_from_config` — `[prompt] layout_aware = true`
    in `kuroi.toml` enables the flag absent a CLI override.
12. `test_cli_no_layout_aware_overrides_config` — `--no-layout-aware`
    on the CLI wins over `true` in config.
13. `test_run_layout_aware_logs_block_count_at_v` — capture log output;
    assert `INFO` line includes `blocks=N` for each batch when the flag
    is on, and does not when off.

### `tests/core/test_chunking.py`

14. `test_subdivided_slice_emits_consistent_block_ids_under_layout_aware`
    — using `slice_page` to produce a half-page slice, layout-aware
    serialization of the slice contains a subset of the block IDs
    present in the full-page serialization, with no spurious IDs and no
    cross-block bleed.

## Documentation

- `CHANGELOG.md` — entry under next release:
  > **feat(prompt):** opt-in `--layout-aware` flag wraps the LLM prompt
  > with PyMuPDF block boundaries (`<block id="N">…</block>`). Helps the
  > model disambiguate field labels from values and reduces context
  > loss across subdivided batches. Off by default; enable with
  > `--layout-aware` or `[prompt] layout_aware = true`.
- `docs/user-guide/prompt-tuning.md` (new short page) — when to enable,
  expected ~5% prompt-token overhead, current limits (no table
  awareness yet, no recurring-element dedup yet — links to the Future
  work section of this design).
- `docs/reference/cli.md` — auto-regenerated from the typer surface.
- `docs/reference/configuration.md` — add `[prompt] layout_aware` to
  the schema reference, in the same shape as `[chunking]`.

## Future work (deliberately out of scope)

Block tags are scaffolding for two follow-on specs that slot into the
same envelope. Both are *not* part of this ship.

### Table-aware overlay

Use `page.find_tables()` (PyMuPDF ≥ 1.23) to detect tables and replace
the surrounding `<block>` with `<table rows="R" cols="C">` containing
`<cell row="r" col="c">` children. Words inside cells keep their
`[idx]token` markers; the response schema is unchanged. This is the
part of the brainstorming history that captures column-vs-name
disambiguation. Deferred because (a) `find_tables()` has its own
false-positive surface and needs a confidence threshold, (b) the schema
delta is meaningfully larger, and (c) we want telemetry from plain
block tags first to know whether tables are the long pole.

### Cross-page recurring-element deduplication

Cross-page diff to detect repeating headers, footers, page numbers,
watermarks, and running heads. On pages 2..N, replace the recurring run
with `<block id="N" repeats-from="1"/>`. The first occurrence carries
the actual words; subsequent pages reference back. The orchestrator's
redaction-mapping step expands the references back into bbox unions
per page. This is where the meaningful **token savings** for
repetitive legal/financial documents live — but it requires a
whole-document preprocessing pass that the current per-page extraction
shape does not accommodate. Deferred until block tags ship and we can
measure how much headroom is left.

### Block-aware chunking boundaries

`subdivide-on-failure` currently halves words at the midpoint with a
50-word overlap. With block IDs in hand, a follow-up could prefer block
boundaries within the M ± OVERLAP zone, halving on the nearest block
edge so neither half cuts a paragraph mid-content. Out of scope here —
this design adds the metadata, doesn't yet consume it in chunking.

## Out of scope

- **Default-on rollout.** Ship the flag off-by-default first, gather
  telemetry, then revisit. Mirrors `--pages-per-batch` and
  `--max-retries` (also opt-in initially).
- **Block-type extraction (text vs image).** PyMuPDF's `block_type`
  distinguishes text (0) from image (1). Image blocks contain no words
  and never appear in the prompt regardless. If image-block-aware
  prompting becomes useful (e.g. "the words below are an OCR of an
  image"), it's a one-line schema addition for a follow-up.
- **Font / size / color signals.** PyMuPDF's `get_text("dict")` exposes
  per-span typographic data. Useful for distinguishing headings, but
  adds a second extraction pass and a richer schema. Defer.
- **Reading-order correction.** Multi-column documents sometimes
  produce out-of-order word streams. PyMuPDF's `sort=True` flag on
  `get_text` partially fixes this; orthogonal to block tagging and
  tracked separately.
- **A/B accuracy benchmark.** Token-overhead telemetry is in scope;
  comparative-accuracy measurement requires the eval harness tracked in
  a separate spec direction. Without that harness, the case for moving
  the default to on (or for committing to the table-aware and
  recurring-dedup follow-ons) is judgement-call rather than
  data-driven, which is the right shape for this initial ship.
