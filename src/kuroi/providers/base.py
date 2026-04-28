"""The Provider Protocol every LLM client implements."""

from __future__ import annotations

from typing import Protocol

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
    ) -> list[Finding]: ...
