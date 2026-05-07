"""The Provider Protocol every LLM client implements."""

from __future__ import annotations

from typing import Protocol

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page


class Provider(Protocol):
    """Interface kuroi uses to talk to any LLM, cloud or local."""

    name: str  # e.g. "anthropic"
    model: str  # e.g. "claude-opus-4-7"

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        """Identify spans to redact across the supplied pages.

        Args:
            pages: Word-indexed pages produced by the PDF extractor.
            llm_category_ids: Category ids the provider is responsible for.
            instructions: Free-text redaction instructions from the user.
            seed: Optional sampling seed for reproducible runs.
            attempt: Zero-based retry index from the chunker. Providers may use
                it to scale per-call timeouts (e.g. Ollama gives slow models
                more time on each retry).

        Returns:
            A tuple of `(findings, chunk_records)` — findings are the proposed
            redactions, chunk_records audit the prompts and raw responses for
            each chunk dispatched to the model.
        """
        ...
