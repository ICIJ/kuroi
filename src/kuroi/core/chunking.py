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
from dataclasses import replace

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers.base import Provider

logger = logging.getLogger("kuroi.core.chunking")

# One entry per retry. Length determines the retry count; the initial attempt
# is implicit, so total attempts = len(RETRY_BACKOFFS_SECONDS) + 1.
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 4.0)
MAX_ATTEMPTS = len(RETRY_BACKOFFS_SECONDS) + 1


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
        attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.batch_idx = batch_idx
        self.page_numbers = page_numbers
        self.attempts = attempts
        super().__init__(
            f"Batch {batch_idx + 1} (pages {_format_page_range(page_numbers)}) "
            f"failed {attempts} times and was aborted."
        )


def detect_redactions_chunked(
    provider: Provider,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    pages_per_batch: int,
    on_batch_start: Callable[[int, int, tuple[int, ...]], None] | None = None,
    on_batch_complete: Callable[[int, int, tuple[int, ...], ChunkRecord], None] | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
    if pages_per_batch < 1:
        raise ValueError(f"pages_per_batch must be >= 1, got {pages_per_batch}")

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
        for attempt in range(MAX_ATTEMPTS):
            findings, chunks = provider.detect_redactions(
                batch,
                llm_category_ids,
                instructions=instructions,
                seed=seed,
                attempt=attempt,
            )
            if chunks:
                break
            if attempt + 1 >= MAX_ATTEMPTS:
                raise BatchError(batch_idx, page_numbers, attempts=MAX_ATTEMPTS)
            backoff = RETRY_BACKOFFS_SECONDS[attempt]
            logger.info(
                "retrying batch %d/%d (pages %s) in %.0fs (attempt %d/%d)",
                batch_idx + 1,
                total_batches,
                _format_page_range(page_numbers),
                backoff,
                attempt + 2,
                MAX_ATTEMPTS,
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
