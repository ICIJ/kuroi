"""Claude CLI provider — drives the local `claude` binary via claude-agent-sdk.

Calls bill against the user's Claude Code subscription (no API key). The
sync `Provider.detect_redactions` interface is bridged to the SDK's async
`query()` via `anyio.run` per call. The event-loop spin-up cost is
negligible compared to the LLM round-trip.

Auth precedence: if `ANTHROPIC_API_KEY` is set in the environment, the CLI
prefers API (per-token) billing over OAuth/subscription. We do NOT strip
the variable; we log a warning at provider init so the user can decide.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers._shared import (
    build_system_prompt,
    build_user_document_block,
    build_user_static_prefix,
    parse_findings_payload,
)

logger = logging.getLogger("kuroi.providers.claude_cli")


class ClaudeCliProvider:
    """Provider that calls `claude` via claude-agent-sdk (subscription billing)."""

    name = "claude-cli"

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-7",
        cli_path: str | None = None,
        timeout_s: int = 300,
        query_fn: Any | None = None,
    ) -> None:
        self.model = model
        self._cli_path = cli_path
        self._timeout_s = timeout_s
        self._query_fn = query_fn  # injection point for tests; None → SDK's `query`
        if os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning(
                "claude-cli provider: ANTHROPIC_API_KEY is set in your "
                "environment, so the CLI may use API (per-token) billing "
                "instead of your subscription. Unset the variable if you "
                "want subscription billing."
            )

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
        model: str | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        del attempt  # accepted for Provider Protocol parity
        if not llm_category_ids and not instructions:
            return [], []
        raise NotImplementedError("filled in by Task 8")
