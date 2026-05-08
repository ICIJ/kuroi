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
from kuroi.core.pdf import Page
from kuroi.providers.base import Provider

logger = logging.getLogger("kuroi.core.chunking")


def _format_page_range(page_numbers: tuple[int, ...]) -> str:
    if len(page_numbers) > 1:
        return f"{page_numbers[0]}–{page_numbers[-1]}"  # noqa: RUF001
    return f"{page_numbers[0]}"


class BatchError(Exception):
    """Raised when a batch fails every attempt (initial call + retries)."""

    def __init__(
        self,
        batch_idx: int,
        page_numbers: tuple[int, ...],
        attempts: int,
    ) -> None:
        self.batch_idx = batch_idx
        self.page_numbers = page_numbers
        self.attempts = attempts
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
