"""Per-batch chunking orchestrator for LLM redaction calls.

Slices a document's pages into batches of N, dispatches one
`Provider.detect_redactions` call per batch, and aggregates the resulting
findings and chunk records. Retry, abort, and progress concerns live here so
provider implementations stay single-call.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import replace

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers.base import Provider

logger = logging.getLogger("kuroi.core.chunking")


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

        findings, chunks = provider.detect_redactions(
            batch,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
        )

        renumbered = [replace(c, chunk_idx=batch_idx) for c in chunks]
        aggregate_findings.extend(findings)
        aggregate_chunks.extend(renumbered)

        if on_batch_complete is not None and renumbered:
            on_batch_complete(batch_idx, total_batches, page_numbers, renumbered[0])

    return aggregate_findings, aggregate_chunks
