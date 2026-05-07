# Per-Batch Page Chunking for LLM Calls

**Date:** 2026-05-07
**Status:** Draft

## Overview

Add support for splitting a document into batches of N pages, where each batch is one LLM call. This avoids silent context-window truncation on small models (qwen3:8b's 8k context vs. a 164k-token document) and is the foundation for any future concurrency or streaming work.

The flag is opt-in. The current single-call behavior remains the default and is unchanged when `--pages-per-batch 0`.

## Motivation

A real run against a 164k-token court exhibit on `ollama/qwen3:8b` returned `{"findings": []}` because the Ollama daemon silently truncated the prompt to the model's default context. The model only saw the system prompt and a few opening pages; the bulk of the document never reached it. The user saw "No redactions proposed" with no indication that anything was wrong.

The observability work that immediately preceded this spec (WARNING-level truncation alerts, INFO-level token/duration logging, DEBUG-level full prompt/response dumps in the providers) makes that failure visible. This spec gives the user a way to actually fix it: keep each LLM call below the model's context window by sending only N pages at a time.

## CLI surface

One new option on `kuroi run`:

```
--pages-per-batch N   Split LLM analysis into batches of N pages.
                      0 (default) keeps the current single-call behavior.
```

| Value | Meaning |
|-------|---------|
| `0`   | Default. Current single-call behavior. No batching, no retry, no orchestrator. |
| `1`   | Strict per-page (one LLM call per page). |
| `N ≥ 2` | Pack N consecutive pages per LLM call. The last batch may be shorter. |

The pre-call cost line gains a batches column when batching is enabled:

```
Estimated cost: $0.0042  (164411 input tokens, anthropic/claude-opus-4-7, 49 pages → 49 batches)
```

The system prompt and any `--instruct` text repeat in every batch's user prompt so the model has full instruction context for each page. Total `input_tokens` summed across batches is therefore slightly higher than the single-call number; this is not currently surfaced in the pre-call estimate (which still tokenizes the whole document once) but the per-call `tokens_in` shown at `-v` will reflect the per-batch cost.

## Architecture

### New module `src/kuroi/core/chunking.py`

Single public function:

```python
def detect_redactions_chunked(
    provider: Provider,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    pages_per_batch: int,                   # ≥ 1; caller decides whether to invoke
    on_batch_start: Callable[[int, int, tuple[int, ...]], None] | None = None,
    on_batch_complete: Callable[[int, int, tuple[int, ...], ChunkRecord], None] | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]
```

Callback contract:

- `on_batch_start(batch_idx, total_batches, page_numbers)` fires once before each batch's first attempt. Not re-fired on retry.
- `on_batch_complete(batch_idx, total_batches, page_numbers, chunk)` fires once after a batch produces a `ChunkRecord` (whether on first attempt or after the single retry). The `chunk` argument carries `duration_ms`, `tokens_in`, `tokens_out` for caller-driven progress UI.
- Neither fires when both attempts hard-fail; `BatchError` is raised instead.

Retries surface via `logger.info("retrying batch %d/%d (pages %s) after 2s", ...)` so they're visible at `-v` without growing the callback surface.

Behavior:

1. Slice `pages` into `ceil(len(pages) / pages_per_batch)` batches. The last batch may be shorter.
2. For each batch (0-indexed `batch_idx`):
   - Call `on_batch_start(batch_idx, total_batches, page_numbers)` if given.
   - Call `provider.detect_redactions(batch, llm_category_ids, instructions=..., seed=...)`.
   - If `chunks == []` (hard failure), `time.sleep(2)` and call once more.
   - If the retry also returns `chunks == []`, raise `BatchError(batch_idx, page_numbers)`.
   - Otherwise extend the aggregate findings list and the aggregate chunks list.
3. After all batches complete, replace each `ChunkRecord.chunk_idx` with its batch position (`0, 1, 2, ...`) using `dataclasses.replace`. Providers hardcode `chunk_idx=0`; the orchestrator owns the renumbering.
4. Return `(aggregate_findings, renumbered_chunks)`.

### New exception `BatchError`

Defined in `src/kuroi/core/chunking.py`:

```python
class BatchError(Exception):
    def __init__(self, batch_idx: int, page_numbers: tuple[int, ...]) -> None:
        ...
        self.batch_idx = batch_idx
        self.page_numbers = page_numbers
```

The message is constructed for direct user display: `"Batch 4 (pages 16–20) failed twice and was aborted."`. `cli/run.py` catches it and surfaces it via `console.print` plus `typer.Exit(code=1)`.

### Why outside the providers

The chunking, retry, progress, and renumbering policies are identical for both providers. Putting them in an orchestrator keeps `Provider.detect_redactions` single-call (matching its current contract) and means one set of tests covers both `AnthropicProvider` and `OllamaProvider`.

`Page.number` already carries the original 1-indexed page number, so a sliced batch like `pages[5:10]` still references "pages 6–10" correctly inside `serialize_for_llm` and `parse_findings_payload`. No provider-side code changes are required for the slicing to work.

### `cli/run.py` glue

Where the file currently calls `provider.detect_redactions(...)` (line 192-194):

- If `pages_per_batch == 0`: keep the current direct call. Zero behavior change for default users.
- If `pages_per_batch >= 1`: call `detect_redactions_chunked(...)` instead, passing a closure as `on_batch_start` that prints the progress line. Catch `BatchError` and convert to `typer.Exit(code=1)` with a message that includes the batch index, page range, and a hint to lower `--pages-per-batch` or check the WARNING above.

The aggregated `chunks` list flows into the existing audit-log loop unchanged (each `ChunkRecord` becomes one `chunk_request` event).

## Failure handling

The orchestrator distinguishes hard from soft outcomes by inspecting the returned `chunks` list — both providers already follow this convention after the observability work:

- **Hard failure** (HTTP error, timeout, connection refused, malformed envelope JSON): provider returns `chunks == []`. Retry once after 2s. If still hard-failing, raise `BatchError`.
- **Soft outcome** (response was JSON, model returned `{"findings": []}` or all findings out-of-range): provider returns `chunks == [chunk]` with `findings == []`. No retry — both providers run at `temperature=0` and a retry will produce the same answer. Truncation/non-JSON WARNINGs already fired on the first call and tell the user the cause.

A 2-second `time.sleep` is the entirety of the backoff. Single retry, single sleep — no exponential growth.

The default (non-chunked) path is untouched. Providers swallow exceptions exactly as they do today; users on `--pages-per-batch 0` continue to see "No redactions proposed" on hard failures (now accompanied by the WARNING logs from the observability work).

## Audit

No schema change. Each batch produces exactly one `ChunkRecord` (which providers already construct). The orchestrator renumbers `chunk_idx` to its batch position so audit consumers can reconstruct order. `pages` on each record lists the page numbers in that batch — `(1, 2, 3, 4, 5)` for `--pages-per-batch 5`.

For a 49-page run with `--pages-per-batch 1`, the audit log gains 49 `chunk_request` events, one per page. For `--pages-per-batch 5`, it gains 10.

## Progress UI

`cli/run.py` builds two closures and passes them as `on_batch_start` / `on_batch_complete`:

- `on_batch_start` prints `"  Batch 4/49 (pages 16-20)... "` (no trailing newline) using the existing `console.print(..., end="")` pattern.
- `on_batch_complete` reads `chunk.duration_ms` and prints the rest of the line. At default verbosity the line ends after `done in 1.2s`. At `-v` (INFO) and above it appends `, tokens_in=2871 tokens_out=412`. The closure inspects the same `verbose` count that `cli/__init__.py` already wires through `setup_logging`.

Final-rendered example at `-v`:

```
  Batch 4/49 (pages 16-20)... done in 1.2s, tokens_in=2871 tokens_out=412
```

The orchestrator itself does not print — keeping `core/chunking.py` free of CLI-layer dependencies. The existing per-call WARNING-level events (truncation, non-JSON) continue to fire per batch from inside the providers and remain visible at default verbosity.

## Tests

New `tests/core/test_chunking.py`:

1. `test_chunked_call_slices_pages_into_batches` — stub provider records each call's `pages`; assert correct slicing for N=2 and N=3 with both even-divisor and remainder cases.
2. `test_chunked_call_aggregates_findings_in_order` — stub returns one finding per batch; assert the combined list preserves batch order.
3. `test_chunked_call_renumbers_chunk_idx` — stub provider hardcodes `chunk_idx=0`; assert orchestrator renumbers to `0, 1, 2, ...` in returned chunks.
4. `test_chunked_call_retries_once_on_hard_failure` — stub returns `[], []` on first call, `[finding], [chunk]` on second; assert total calls == 2, finding preserved.
5. `test_chunked_call_aborts_after_two_failures` — stub returns `[], []` twice; assert `BatchError` raised with correct `batch_idx` and `page_numbers`.
6. `test_chunked_call_does_not_retry_on_soft_empty` — stub returns `[], [chunk]`; assert single call (no retry), empty findings accumulated, chunk record kept.

Add to `tests/cli/test_run.py`:

7. `test_run_with_pages_per_batch_calls_chunking_orchestrator` — uses the existing fake provider; assert `--pages-per-batch 2` on a 4-page document produces 2 batches and the audit log has 2 `chunk_request` events.

The retry sleep in test #4 is patched to a no-op via `monkeypatch.setattr` on `time.sleep`.

## Out of scope

Explicitly deferred to keep this spec focused:

- **Concurrency.** Sequential only. Bounded-concurrent and provider-tuned variants were considered and rejected for v1 (added complexity, no benefit on Ollama which serializes model loading anyway).
- **Auto-detection** based on input token size. Considered and rejected — the threshold is provider/model dependent and adds magic that's hard to debug. Users opt in explicitly.
- **Token-budgeted batches** (pack pages until N tokens). The user prefers fixed page counts.
- **Cross-batch prompt caching.** The system prompt is identical across batches and would benefit from Anthropic's prompt cache, but wiring this requires an SDK-side change and is independent of the chunking logic.
- **Skip-failed-batch mode.** Considered and rejected — for a redaction tool, "missed page" silently shipped = leak. Abort is the safer default; if users want skip-mode later, that is a separate flag.
- **Persisting the `chunk_request` audit on the empty-findings early-exit in `cli/run.py:198`.** This is a real gap (calls back to the original observability thread) but is independent of chunking and belongs in its own change.
