"""Anthropic provider — prompt construction, response parsing, and SDK call.

The class lives at the bottom; the prompt/parse helpers are module-level so
they can be unit-tested against pure data with no SDK involvement.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers._shared import (
    build_system_blocks,
    build_user_document_block,
    build_user_prompt,
    build_user_static_prefix,
    parse_findings_payload,
)

__all__ = ["AnthropicProvider", "build_user_prompt", "parse_findings_payload"]


def _is_prompt_too_long(exc: object) -> bool:
    """True iff an Anthropic BadRequestError signals an oversized prompt.

    The SDK exposes the structured body on `exc.body`. We match on
    error.type == "invalid_request_error" *and* the substring
    "prompt is too long" anywhere in the message — narrower than catching
    every 400, which would swallow real misconfiguration (unknown model,
    malformed schema, etc.).
    """
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error")
    if not isinstance(error, dict):
        return False
    if error.get("type") != "invalid_request_error":
        return False
    message = error.get("message", "")
    return isinstance(message, str) and "prompt is too long" in message.lower()


logger = logging.getLogger("kuroi.providers.anthropic")

# claude-opus-4-x and newer extended-thinking models reject temperature
_NO_TEMPERATURE_MODELS = {"claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5"}

# Above this fraction of max_tokens we assume the response was truncated.
_TRUNCATION_THRESHOLD = 0.95


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
        attempt: int = 0,
        layout_aware: bool = False,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        del (
            attempt
        )  # accepted for Provider protocol compliance; Anthropic SDK has its own retry/timeout
        if not llm_category_ids and not instructions:
            return [], []
        static_prefix = build_user_static_prefix(llm_category_ids, instructions)
        document_block = build_user_document_block(pages, layout_aware=layout_aware)
        # Hash the joined prompt for audit-log continuity with prior runs.
        prompt_sha = hashlib.sha256(
            (static_prefix + document_block).encode("utf-8")
        ).hexdigest()

        extra: dict[str, Any] = {} if self.model in _NO_TEMPERATURE_MODELS else {"temperature": 0}

        logger.debug(
            "anthropic request model=%s max_tokens=%d prompt_chars=%d prompt_sha=%s\n"
            "FULL PROMPT:\n%s",
            self.model,
            self._max_tokens,
            len(static_prefix) + len(document_block),
            prompt_sha[:8],
            static_prefix + document_block,
        )

        import anthropic  # local import to keep optional at module load

        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                system=build_system_blocks(layout_aware),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": static_prefix,
                                "cache_control": {"type": "ephemeral"},
                            },
                            {
                                "type": "text",
                                "text": document_block,
                            },
                        ],
                    }
                ],
                **extra,
            )
        except anthropic.BadRequestError as exc:
            if _is_prompt_too_long(exc):
                logger.warning(
                    "anthropic rejected prompt as too long (will subdivide): %s",
                    exc,
                )
                return [], []
            raise
        duration_ms = int((time.monotonic() - started) * 1000)

        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        response_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0)) if usage else 0
        tokens_out = int(getattr(usage, "output_tokens", 0)) if usage else 0

        logger.info(
            "anthropic response duration_ms=%d tokens_in=%d tokens_out=%d response_chars=%d",
            duration_ms,
            tokens_in,
            tokens_out,
            len(text),
        )
        logger.debug("anthropic response sha=%s\nFULL RESPONSE:\n%s", response_sha[:8], text)

        if tokens_out and tokens_out >= int(self._max_tokens * _TRUNCATION_THRESHOLD):
            logger.warning(
                "anthropic response truncated at max_tokens (will subdivide): "
                "tokens_out=%d hit %.0f%% of max_tokens=%d. The JSON was probably "
                "cut mid-array; subdividing this batch and retrying.",
                tokens_out,
                100 * tokens_out / self._max_tokens,
                self._max_tokens,
            )
            return [], []

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
        except json.JSONDecodeError as exc:
            logger.warning(
                "anthropic returned non-JSON response (%s). The model may have wrapped "
                "the JSON in markdown or added preamble. First 500 chars: %r",
                exc,
                text[:500],
            )
            return [], [chunk]

        source = "instruction" if not llm_category_ids else "llm"
        return parse_findings_payload(payload, pages, source=source), [chunk]
