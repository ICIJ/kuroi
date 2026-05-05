"""Anthropic provider — prompt construction, response parsing, and SDK call.

The class lives at the bottom; the prompt/parse helpers are module-level so
they can be unit-tested against pure data with no SDK involvement.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers._shared import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_findings_payload,
)

__all__ = ["AnthropicProvider", "build_user_prompt", "parse_findings_payload"]

# claude-opus-4-x and newer extended-thinking models reject temperature
_NO_TEMPERATURE_MODELS = {"claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5"}


class AnthropicProvider:
    """Real Anthropic-API-backed Provider implementation.

    The HTTP call is delegated to the official `anthropic` SDK. Tests construct
    this class with a `client` argument that mocks `messages.create`.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-7",
        api_key: str | None = None,
        client: Any | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        if client is not None:
            self._client = client
        else:
            import anthropic  # local import; do not require SDK at import time

            self._client = anthropic.Anthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
            )

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        if not llm_category_ids and not instructions:
            return [], []
        user_prompt = build_user_prompt(pages, llm_category_ids, instructions)
        prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()

        extra: dict[str, Any] = (
            {} if self.model in _NO_TEMPERATURE_MODELS else {"temperature": 0}
        )

        started = time.monotonic()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            **extra,
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        response_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0)) if usage else 0
        tokens_out = int(getattr(usage, "output_tokens", 0)) if usage else 0

        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=False,  # Anthropic SDK does not expose seed
            system_fingerprint=getattr(response, "system_fingerprint", None),
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [], [chunk]

        source = "instruction" if not llm_category_ids else "llm"
        return parse_findings_payload(payload, pages, source=source), [chunk]
