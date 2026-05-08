"""Per-batch chunking orchestrator for LLM redaction calls.

Slices a document's pages into batches of N, dispatches one
`Provider.detect_redactions` call per batch, and aggregates the resulting
findings and chunk records. Retry, abort, and progress concerns live here so
provider implementations stay single-call.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.config import RetryPolicy
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, slice_page
from kuroi.providers.base import Provider

logger = logging.getLogger("kuroi.core.chunking")

MIN_CHUNK_WORDS = 50
OVERLAP_WORDS = 50


def _format_page_range(page_numbers: tuple[int, ...]) -> str:
    if len(page_numbers) > 1:
        return f"{page_numbers[0]}–{page_numbers[-1]}"  # noqa: RUF001
    return f"{page_numbers[0]}"


class BatchError(Exception):
    """Raised when a batch fails every attempt (initial call + retries),
    optionally after exhausting all subdivision levels.

    The legacy single-line message
    `"Batch N (pages X-Y) failed K times and was aborted."` is preserved
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
    # Mirror left: each child has size (mid + OVERLAP_WORDS). For odd n
    # this keeps both halves strictly smaller than the parent.
    right_local_start = n - left_local_end

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

    schedule = retry_policy.schedule()
    total_attempts = len(schedule) + 1

    total_batches = math.ceil(len(pages) / pages_per_batch)
    aggregate_findings: list[Finding] = []
    aggregate_chunks: list[ChunkRecord] = []

    for batch_idx in range(total_batches):
        offset = batch_idx * pages_per_batch
        batch = pages[offset : offset + pages_per_batch]
        page_numbers = tuple(p.number for p in batch)

        if on_batch_start is not None:
            on_batch_start(batch_idx, total_batches, page_numbers)

        findings: list[Finding] = []
        chunks: list[ChunkRecord] = []
        for attempt in range(total_attempts):
            findings, chunks = provider.detect_redactions(
                batch,
                llm_category_ids,
                instructions=instructions,
                seed=seed,
                attempt=attempt,
            )
            if chunks:
                break
            if attempt + 1 >= total_attempts:
                raise BatchError(batch_idx, page_numbers, attempts=total_attempts)
            backoff = schedule[attempt]
            logger.info(
                "retrying batch %d/%d (pages %s) in %.0fs (attempt %d/%d)",
                batch_idx + 1,
                total_batches,
                _format_page_range(page_numbers),
                backoff,
                attempt + 2,
                total_attempts,
            )
            time.sleep(backoff)

        renumbered = [replace(c, chunk_idx=batch_idx) for c in chunks]
        aggregate_findings.extend(findings)
        aggregate_chunks.extend(renumbered)

        if renumbered:
            assert len(renumbered) == 1, (
                f"providers must return exactly one ChunkRecord per call, got {len(renumbered)}"
            )
        if on_batch_complete is not None and renumbered:
            on_batch_complete(batch_idx, total_batches, page_numbers, renumbered[0])

    return aggregate_findings, aggregate_chunks
