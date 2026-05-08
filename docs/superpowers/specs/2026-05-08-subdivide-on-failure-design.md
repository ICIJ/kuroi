# Subdivide-on-Failure for Oversized LLM Batches

**Date:** 2026-05-08
**Status:** Draft

## Overview

When an LLM call fails after exhausting the configured retry policy, the
chunking orchestrator currently raises `BatchError` and aborts the entire
run — wasting work already done on prior batches and abandoning batches
not yet attempted. This spec adds **reactive subdivision**: when a batch
still fails after retries, the orchestrator halves it and recurses.
Multi-page batches halve by pages; single pages halve by word range with
a small overlap window so entities straddling the cut survive. Subdivision
terminates at a hard floor (minimum chunk size); below the floor we abort
with precise diagnostics.

The change is contained to `src/kuroi/core/chunking.py` plus a small
slicing helper in `core/pdf.py`, two error-path tweaks in
`providers/anthropic.py`, three in `providers/ollama.py`, and a new
optional field on `ChunkRecord`. The provider interface, CLI surface,
and config schema are unchanged. The redaction, diff, verify, and audit
pipelines downstream are unchanged.

## Motivation

Current behavior on a 56-page document where page 41 is too dense for the
active model (e.g. local Ollama on a slow CPU):

1. Page 41's batch hits the Ollama 120 s read timeout (attempt 0) and
   returns `[], []`.
2. The chunker retries with growing timeouts (240 s, 360 s) per the
   default `RetryPolicy(max_retries=2)`. The same too-big prompt is sent
   each time, so it times out each time.
3. After ~12 minutes of wall-clock retry, the chunker raises `BatchError`.
   The output PDF is never written. Pages 42–56 are never attempted.
   Pages 1–40 produced findings that get discarded with the run.

Three failure modes drive this design:

1. **Pages too dense for the model's wall-clock throughput.** Ollama
   timeouts at 120 s on a single page even when the prompt fits the
   context window — most common on CPU-only deployments with large local
   models.
2. **Pages too long for the model's context window.** Anthropic returns
   `BadRequestError("prompt is too long")`. Today this exception
   propagates out of the provider and crashes the run with a stack trace.
3. **Output truncation at `max_tokens`.** When the model finds more
   redactions than `max_tokens=4096` allows, the JSON response is cut
   mid-array. Today a warning is logged and the truncated chunk is
   silently emitted as `[], [chunk]` — the chunker treats it as success
   and findings are silently lost.

In all three cases, halving the batch and retrying the halves is the
correct response: it shrinks both the prompt size and the expected output
size proportionally.

## Subdivision rules

`_halve(item)` returns the two children to recurse on, or raises
`BatchError` at the floor. Three cases evaluated in order:

### Case 1 — multi-page batch (≥ 2 full pages)

Split the page list down the middle. `[p1, p2, p3, p4]` →
`[[p1, p2], [p3, p4]]`. Each child is still a full-page WorkItem with
slice offset 0. No overlap — page boundaries are already meaningful
boundaries in the document, so no entity can straddle them.

### Case 2 — single page with N words

Slice the words with a symmetric overlap window. Constants:

```python
MIN_CHUNK_WORDS = 50
OVERLAP_WORDS = 50
```

With midpoint `M = N // 2`:

- Left child: words `[0 .. M + OVERLAP_WORDS]`
- Right child: words `[M - OVERLAP_WORDS .. N]`

Both children cover the boundary zone `[M − O, M + O]`, so any entity
spanning M appears in at least one child. Findings from the overlap zone
may surface in both children; dedupe (below) collapses them.

### Case 3 — floor

Halving is only useful if both children are strictly smaller than the
parent. With overlap O, child size is `N/2 + O`, so we need `N/2 + O < N`,
i.e. `N > 2·O`. Equivalently:

```python
if N <= 2 * OVERLAP_WORDS or N // 2 + OVERLAP_WORDS < MIN_CHUNK_WORDS:
    raise BatchError(...)
```

With defaults `OVERLAP_WORDS = MIN_CHUNK_WORDS = 50` the effective floor
is **N ≤ 100 words on a single page**. Multi-page batches never hit this
case (they fall through to Case 2 once they've subdivided down to single
pages).

## Architecture

### Control flow

The top-level loop in `detect_redactions_chunked` is unchanged in spirit
— still iterates over `total_batches` page-batches — but delegates each
batch to a new `_try_or_subdivide` helper. Subdivision is **post-retry**:
transient flakes (HTTP 500, malformed JSON) get the full `RetryPolicy`
budget at every recursion level. Only after retries are exhausted do we
conclude "this batch is too big" and split.

```python
def detect_redactions_chunked(...):
    chunk_idx_counter = _IndexCounter()
    for batch_idx in range(total_batches):
        batch = pages[offset : offset + pages_per_batch]
        if on_batch_start: on_batch_start(batch_idx, total_batches, page_numbers)
        item = WorkItem.from_pages(batch)
        findings, chunks = _try_or_subdivide(
            item, provider, retry_policy, chunk_idx_counter,
            llm_category_ids, instructions, seed,
        )
        aggregate_findings.extend(findings)
        aggregate_chunks.extend(chunks)
        if on_batch_complete and chunks:
            on_batch_complete(batch_idx, total_batches, page_numbers, chunks[-1])

def _try_or_subdivide(item, provider, retry_policy, counter, ...) -> (findings, chunks):
    findings, chunks = _try_with_retries(item, provider, retry_policy, ...)
    if chunks:                                          # success
        translated = _translate_indices(findings, item)
        renumbered = [replace(c, chunk_idx=counter.next(), page_word_range=item.word_range)
                      for c in chunks]
        return translated, renumbered
    halves = _halve(item)                               # raises BatchError at floor
    out_f, out_c = [], []
    for half in halves:
        f, c = _try_or_subdivide(half, provider, retry_policy, counter, ...)
        out_f.extend(f); out_c.extend(c)
    return _dedupe(out_f), out_c
```

### Recursion bound

A K-page batch subdivides at most `log2(K)` levels by pages, then a
single page with N words subdivides at most `log2(N / MIN_CHUNK_WORDS)`
levels. With K = 10, N = 8000: at most ~10 levels deep. Bounded and
terminates in a finite number of API calls (worst case: roughly 2N /
MIN_CHUNK_WORDS = 320 calls for N = 8000, but realistically far fewer
because larger slices tend to succeed before subdivision pushes that deep).

### Progress callbacks

`on_batch_start` / `on_batch_complete` continue to fire **at the
top-level batch boundary only**. Subdivision is internal — the user sees
"batch 3/10 complete" once the entire subdivided tree resolves. This
matches the existing UX and avoids progress-bar churn during recursion.
The `ChunkRecord` passed to `on_batch_complete` is the **last** sub-call
of the batch (so users see at least one record per batch); the audit log
captures all sub-calls in order.

## Components

### `src/kuroi/core/pdf.py` — `Page.slice` helper

```python
def slice_page(page: Page, word_start: int, word_end: int) -> Page:
    """Return a synthetic page with words[word_start:word_end] re-indexed
    to 0..(word_end - word_start - 1). The page number is preserved.

    Re-indexing matters: the LLM sees `[idx]token` markers from
    serialize_for_llm, and the response indices are validated as positions
    in page.words by parse_findings_payload. By resetting idx to 0..N-1
    inside the slice, the synthetic page round-trips through the existing
    serialization and parsing without any other change. The chunker
    translates the returned indices back to the original page by adding
    word_start.
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

### `src/kuroi/core/chunking.py` — `WorkItem` and helpers

Module-private dataclass — not exposed to providers:

```python
@dataclass(frozen=True)
class _WorkItem:
    """One unit dispatched as a single Provider.detect_redactions call.

    For multi-page batches: pages is the full-page tuple, word_range is None.
    For sub-page slices: pages is a single synthetic (re-indexed) page,
    word_range is (original_start, original_end) for index translation
    and audit-log recording.
    """
    pages: tuple[Page, ...]
    word_range: tuple[int, int] | None  # None = full pages

    @classmethod
    def from_pages(cls, pages: tuple[Page, ...]) -> "_WorkItem":
        return cls(pages=pages, word_range=None)
```

`_halve(item)` implements the three subdivision cases above.

`_translate_indices(findings, item)` adds `word_range[0]` to every
finding's `start`/`end` when `item.word_range is not None`; otherwise
returns findings unchanged. The `page` field is already correct because
`slice_page` preserves `page.number`.

`_dedupe(findings)` collapses identical `(page, start, end, kind)` tuples
keeping the highest confidence (`high > medium > low`). It does **not**
merge overlapping-but-non-identical findings — overlapping ranges of the
same kind are preserved on purpose; the redaction step naturally redacts
the union, which is the safer outcome for a security tool.

`_IndexCounter` is a tiny stateful integer that the orchestrator threads
through recursion to assign globally-unique, monotonically-increasing
`chunk_idx` values across all sub-calls in a run. Replaces the existing
`chunk_idx = batch_idx` assignment.

The existing `assert len(renumbered) == 1` at `chunking.py:111` is
removed. Per-call the providers still return exactly one `ChunkRecord`,
but the orchestrator now accumulates multiple records per top-level
batch (one per successful sub-call). The per-call invariant moves into
`_try_or_subdivide`'s success path:

```python
if chunks:
    assert len(chunks) == 1, (
        f"providers must return exactly one ChunkRecord per call, got {len(chunks)}"
    )
```

### `src/kuroi/core/audit_records.py` — extend `ChunkRecord`

All existing fields stay required (no defaults), so the new field has
to land at the end of the dataclass to satisfy Python's "defaulted
fields must come last" rule:

```python
@dataclass(frozen=True)
class ChunkRecord:
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
    page_word_range: tuple[int, int] | None = None  # NEW; None = full-page call
```

`page_word_range` is `None` for full-page calls (the common case) and
`(start, end)` in **original-page coordinates** for sub-page calls. The
chunker fills it from `_WorkItem.word_range` after the provider returns,
not the provider — providers stay slice-unaware. All existing call
sites that construct `ChunkRecord` (in both providers) continue to work
without modification because the new field has a default.

The audit-log JSON schema gains an optional `page_word_range` field.
Existing log readers that don't know about it ignore it; readers that do
can reconstruct subdivision exactly.

### `src/kuroi/providers/anthropic.py` — failure-signal alignment

Two changes; both align Anthropic's behavior with the chunker contract
"`chunks == []` means subdivide":

**1. Catch `BadRequestError("prompt is too long")`.** Wrap the
`messages.create` call:

```python
try:
    response = self._client.messages.create(...)
except anthropic.BadRequestError as exc:
    if _is_prompt_too_long(exc):
        logger.warning(
            "anthropic rejected prompt as too long (will subdivide): %s", exc
        )
        return [], []
    raise
```

`_is_prompt_too_long(exc)` inspects `exc.body.get("error", {}).get("type")`
for `"invalid_request_error"` and the message for the substring
`"prompt is too long"`. Other `BadRequestError`s (malformed schema,
unknown model, etc.) keep raising — they're real bugs, and subdivision
won't fix them.

**2. Truncation now signals subdivision.** Today
`anthropic.py:117-125` warns when `tokens_out >= 0.95 * max_tokens` but
still returns `[], [chunk]` (silent data loss — chunker thinks
"success, zero findings"). Change: when the truncation threshold is hit,
return `[], []` so the chunker subdivides. The warning log line is kept
for diagnostics. This is a behavior change worth a CHANGELOG entry; it
converts silent data loss into automatic recovery.

### `src/kuroi/providers/ollama.py` — failure-signal alignment

The four network-level failure paths (TimeoutException, HTTPStatusError,
generic HTTPError, JSONDecodeError on the envelope at
`ollama.py:116-135`) already return `[], []` — no change needed there.

**But three response-content paths today return `[], [chunk]`** when the
HTTP call itself succeeded but the body was malformed
(`ollama.py:167-184`):

- `message.content` missing or not a string
- `content` is not valid JSON despite `format: "json"`
- The parsed payload is not a JSON object

These have the same silent-data-loss shape as Anthropic's truncation
case: the chunker sees a non-empty `chunks` list, considers the call a
success, and emits zero findings. With format=json this is rare in
practice, but it does happen — most often on output truncation when the
local model overshoots its context budget.

Change all three paths to return `[], []` so subdivision triggers. The
warning log lines stay for diagnostics. This is a behavior change in the
same vein as the Anthropic truncation fix — covered by the same
CHANGELOG entry.

## Behavior change summary

| Scenario | Today | After |
| --- | --- | --- |
| Ollama batch times out repeatedly | Run aborts after ~12 min | Batch halves; tries shorter prompts |
| Anthropic returns `prompt is too long` | Crash with stacktrace | Treated as failure, subdivides |
| Anthropic response truncated at `max_tokens` | Findings silently lost | Treated as failure, subdivides |
| Single page below floor still fails | (would crash on stacktrace) | `BatchError` with precise diagnostics |
| Healthy batch on first try | `chunk_idx == batch_idx`, no `page_word_range` field | `chunk_idx` from global counter, `page_word_range = None` |

For runs where no batch subdivides, the global counter still produces
`0, 1, …, total_batches - 1` — identical to today's `batch_idx`
sequence. The values diverge only when at least one batch subdivides
into multiple sub-calls. The new `page_word_range = None` field is
JSON-omittable (default value), so audit-log readers that don't know
about it ignore it.

## Floor error message

When `_halve` raises at the floor, the message exposes everything the
user needs to diagnose:

```
BatchError: page 41 (8000 words) could not be processed even after
subdividing to the minimum chunk size (100 words).

Tried 7 levels of subdivision. The last failed slice was page 41 words
0..100 (prompt_chars=4823, attempts=3).

Likely causes:
  - The model can't keep up with this prompt size in the available timeout
  - Output truncation: the model has more findings than max_tokens allows
  - The page contains content the model rejects (e.g., policy refusals)

Suggestions:
  - Use a model with larger context / faster throughput
  - Increase the retry budget: --max-retries 5
```

(The Ollama provider's per-attempt read timeout grows linearly with
attempt number — 120 s × (attempt + 1) — so raising `--max-retries`
on Ollama also extends per-attempt wall-clock. On Anthropic the
SDK manages its own timeouts; raising `--max-retries` only adds
attempts, not extra time per attempt.)

`BatchError` gains optional fields `subdivision_levels: int`,
`last_failed_word_range: tuple[int, int] | None`, and
`last_prompt_chars: int` so the CLI surface in `cli/run.py` can format
the message cleanly. The single-line legacy message
(`"Batch N (pages X–Y) failed K times and was aborted."`) is preserved
for the multi-page case.

## Configuration

**Hardcoded defaults to start.** `MIN_CHUNK_WORDS = 50` and
`OVERLAP_WORDS = 50` live as module constants in `core/chunking.py`. They
are **not** exposed as CLI flags, env vars, or TOML keys in this ship.

Rationale matches the trajectory of `RetryPolicy`: that was originally
hardcoded too, and configurability was added in
`2026-05-08-cli-retry-config-design.md` only after real-world tuning need
emerged. YAGNI here. If users hit the floor on a meaningful fraction of
documents, we add a `[chunking]` config block following the existing
retry pattern in a follow-up.

## Audit

`ChunkRecord.page_word_range` is the only schema change. A run on a
56-page document where page 41 had to subdivide twice produces:

- 40 chunk records for pages 1–40, `page_word_range = None`
- 2 records for page 41 with e.g. `page_word_range = (0, 4050)` and
  `(3950, 8000)` — both for `pages = (41,)`
- 14 records for pages 42–56, `page_word_range = None`

Total: 56 records (one per successful API call). Failed-then-subdivided
attempts produce no records — same as the existing failed-retry behavior.
A reader that wants to reconstruct subdivision groups records by
`pages` and looks at `page_word_range`.

`chunk_idx` is now globally monotonic. Two records with the same `pages`
tuple are distinguished by `chunk_idx` and `page_word_range`.

## Determinism

With `temperature = 0` and a fixed seed, two runs of the same document
produce identical subdivision trees: the same prompts → the same
failures → the same halving → the same recursive sub-calls. End-to-end
reproducibility is preserved. The existing
`tests/.../test_determinism.py` suite (where present) does not need
changes; it should continue to pass byte-for-byte on documents that
don't trigger subdivision, and produce stable trees on documents that do.

## Tests

### `tests/core/test_chunking_subdivision.py` *(new file)*

A `ScriptedFailureProvider` test fixture that fails on prompts above a
threshold N characters and succeeds otherwise. Cases:

1. `test_multi_page_batch_halves_on_failure` — 4-page batch, fail on
   any 2+ page batch, succeed on single pages → 4 successful single-page
   calls; correct findings; `chunk_idx` values 0–3 monotonically.
2. `test_single_page_halves_with_overlap` — 1-page batch with 1000
   words; fail on any prompt longer than ~half; capture the prompt
   strings sent on each sub-call and assert that the boundary words
   (text content from word indices `M − OVERLAP_WORDS .. M + OVERLAP_WORDS`
   in the original page) appear in the prompts of *both* halves. The
   assertion is on `Word.text` content, not `Word.idx`, because
   `slice_page` re-indexes `idx` to 0..N-1 in the synthetic page.
3. `test_overlap_dedupes_boundary_findings` — both halves return a
   finding for the same overlap-zone word range; final result has 1
   finding; confidence = max of the two.
4. `test_overlapping_non_identical_findings_preserved` — half A returns
   `(page=1, start=10, end=12, kind="X")`, half B returns
   `(page=1, start=11, end=13, kind="X")`; both kept.
5. `test_floor_raises_batch_error_with_diagnostics` — single page with
   80 words; provider always fails; `BatchError` raised with
   `subdivision_levels >= 1`, `last_failed_word_range == (0, 80)`.
6. `test_chunk_idx_globally_monotonic_across_subdivision` — multi-page
   batch where page 2 subdivides once; resulting `chunk_idx` sequence is
   `0, 1, 2, 3, 4` with no duplicates and no gaps.
7. `test_page_word_range_recorded_for_sub_page_calls` — sub-page chunks
   have `page_word_range == (start, end)` in original-page coordinates;
   full-page chunks have `page_word_range is None`.
8. `test_subdivision_post_retry_not_pre_retry` — provider scripted to
   fail twice then succeed; `RetryPolicy(max_retries=2)`; verify the
   batch succeeds without subdivision (retry budget consumed first).
9. `test_progress_callbacks_fire_at_batch_boundary_only` — capture
   `on_batch_start` / `on_batch_complete` calls; one each per top-level
   batch even when subdivision occurs internally.

### `tests/core/test_chunking.py` *(existing file; regression-locked)*

- All existing tests pass unchanged in spirit; the assertion that
  `chunks[0].chunk_idx == batch_idx` is updated to assert global
  monotonicity instead.
- Add `test_full_page_batch_emits_page_word_range_none` to lock the
  default-case audit shape.

### `tests/core/test_pdf.py` *(existing file; new tests added)*

- `test_slice_page_reindexes_words_to_zero_base`
- `test_slice_page_preserves_page_number`
- `test_slice_page_preserves_word_text_and_bbox`
- `test_slice_page_rejects_invalid_ranges` — empty, negative, beyond
  end-of-page

### `tests/providers/test_anthropic.py`

- `test_prompt_too_long_returns_empty_to_signal_subdivide` — patch SDK
  to raise `anthropic.BadRequestError` with the canonical body; provider
  returns `[], []`; warning logged.
- `test_other_bad_request_errors_still_raise` — `BadRequestError`
  whose body is *not* "prompt is too long" propagates.
- `test_truncated_response_now_returns_empty` — regression-locks the
  behavior change: when `tokens_out >= 0.95 * max_tokens`, return
  `[], []` (was `[], [chunk]`).

### `tests/providers/test_ollama.py`

- `test_timeout_returns_empty_chunks_to_signal_subdivide` — regression-lock
  on the four already-correct network failure paths.
- `test_missing_message_content_returns_empty` — patch envelope with no
  `message.content`; provider returns `[], []` (was: `[], [chunk]`).
- `test_non_json_content_returns_empty` — `format=json` honored at the
  HTTP level but content is not valid JSON; provider returns `[], []`.
- `test_non_object_payload_returns_empty` — content parses to a JSON
  array or scalar; provider returns `[], []`.

## Documentation

- `CHANGELOG.md` — entry under next release:
  > **feat(chunking):** automatic subdivision on batch failure. When a
  > batch still fails after retries, kuroi now halves it (by pages, then
  > by word range with a 50-word overlap) and recurses, instead of
  > aborting the run. Single pages too dense for the active model
  > continue to surface a clear `BatchError` with diagnostics.
  > **fix(anthropic):** previously, responses truncated at `max_tokens`
  > were silently emitted as zero findings. They now trigger
  > subdivision, recovering the lost findings on the second pass.
  > **fix(anthropic):** `prompt is too long` errors no longer crash the
  > run; they are caught and trigger subdivision.
  > **fix(ollama):** malformed-content responses (missing `message.content`,
  > non-JSON body despite `format: "json"`, non-object payload) previously
  > emitted a chunk record with zero findings, masking data loss. They
  > now trigger subdivision instead.
- `docs/user-guide/troubleshooting.md` — short paragraph:
  > Hitting `BatchError: ... could not be processed even after
  > subdividing to the minimum chunk size`? The active model can't
  > handle even a small slice of that page. Try a model with a larger
  > context window (Anthropic Sonnet/Opus, larger Ollama models), or
  > raise `--max-retries` to give a slow Ollama deployment more wall
  > clock per attempt.
- `docs/reference/cli.md` — auto-regenerated. No manual edit; no new
  flags.

No `docs/reference/audit.md` exists today; the `chunk_request` event
schema lives in
`docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`
(referenced from `core/audit_records.py:5`). Update that spec's
schema section to mark `page_word_range` as a new optional field.

## Out of scope

- **Pre-flight token estimation.** This design is reactive only. A
  pre-flight estimator (chars-as-tokens heuristic, or a real tokenizer)
  could avoid spending the timeout budget on doomed-to-fail attempts,
  but would add an estimation-vs-truth calibration concern. Revisit if
  the wall-clock cost of reactive-only proves painful in practice.
- **`--skip-pages` / `--on-unprocessable-batch=skip`.** When the floor
  is hit, this design aborts the run. A user-facing skip-and-continue
  flag (with audit-log marker for un-redacted pages) is a natural
  follow-up but explicitly excluded here: one design, one shape.
- **Configuring `MIN_CHUNK_WORDS` / `OVERLAP_WORDS`.** Hardcoded
  defaults to start; expose to `[chunking]` TOML if real-world tuning
  need emerges.
- **Parallel sub-batch dispatch.** Subdivision recurses sequentially.
  Concurrent dispatch (worker pool drained by a queue) is a viable
  optimization; deliberately not bundled here. The recursive shape
  refactors cleanly into a queue if/when needed.
- **Per-attempt audit records for failed sub-calls.** Today, failed
  retries don't produce `ChunkRecord`s. Same here: failed-then-subdivided
  attempts leave no audit trail. Revisit if reproducibility audits
  require it.
- **Sentence-aware splits.** This design uses raw word-count halving
  with overlap. A heuristic that prefers sentence boundaries (split on
  `.`/`!`/`?` words) might be marginally better for context but adds
  complexity. The 50-word overlap window is broad enough to cover
  most boundary entities; revisit if overlap proves insufficient.
- **Coordinating with `--pages-per-batch=0` (default unchunked path).**
  After `2026-05-08-cli-retry-config-design.md`, every run goes through
  the orchestrator. Subdivision applies uniformly; no special-casing
  needed.
