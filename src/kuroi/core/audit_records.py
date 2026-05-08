"""Per-LLM-call audit metadata.

`ChunkRecord` is what every Provider returns alongside its findings. The
fields map 1:1 to the `chunk_request` event schema in
docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md (section 2.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkRecord:
    """A single LLM-API call. There is one ChunkRecord per detect_redactions call;
    the chunking orchestrator emits one ChunkRecord per successful sub-call when
    a batch is subdivided."""

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
    page_word_range: tuple[int, int] | None = None
