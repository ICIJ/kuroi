"""Ollama provider — native HTTP to a local or remote Ollama daemon."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers._shared import (
    build_system_prompt,
    build_user_prompt,
    parse_findings_payload,
)

logger = logging.getLogger("kuroi.providers.ollama")

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 120.0


def _read_timeout_for_attempt(attempt: int) -> float:
    """Linear-grow timeout per chunker retry: 120s, 240s, 360s, ...

    Slow CPUs and large models routinely overshoot 120s, so each retry gives
    the model proportionally more time to respond rather than retrying
    with the same too-tight bound.
    """
    return READ_TIMEOUT_SECONDS * (attempt + 1)


class OllamaProvider:
    """Provider that calls Ollama's `/api/chat` endpoint with `format: "json"`."""

    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        url: str,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._url = url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=READ_TIMEOUT_SECONDS,
                pool=READ_TIMEOUT_SECONDS,
            ),
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
        if not llm_category_ids and not instructions:
            return [], []
        user_prompt = build_user_prompt(
            pages, llm_category_ids, instructions, layout_aware=layout_aware
        )
        prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()

        options: dict[str, Any] = {"temperature": 0}
        if seed is not None:
            options["seed"] = seed
        body = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": options,
            "messages": [
                {"role": "system", "content": build_system_prompt(layout_aware)},
                {"role": "user", "content": user_prompt},
            ],
        }

        read_timeout = _read_timeout_for_attempt(attempt)
        request_timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_SECONDS,
            read=read_timeout,
            write=read_timeout,
            pool=read_timeout,
        )

        logger.debug(
            "ollama request model=%s url=%s prompt_chars=%d prompt_sha=%s "
            "attempt=%d read_timeout=%.0fs\nFULL PROMPT:\n%s",
            self.model,
            self._url,
            len(user_prompt),
            prompt_sha[:8],
            attempt,
            read_timeout,
            user_prompt,
        )

        started = time.monotonic()
        try:
            response = self._client.post(
                f"{self._url}/api/chat", json=body, timeout=request_timeout
            )
            response.raise_for_status()
            envelope = response.json()
        except httpx.TimeoutException as exc:
            logger.warning(
                "ollama request timed out after %.0fs (read timeout, attempt %d). "
                "The model is either not loaded yet or the prompt is too large to "
                "process in time. Detail: %s",
                read_timeout,
                attempt,
                exc,
            )
            return [], []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "ollama returned HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            return [], []
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("ollama call failed: %s: %s", type(exc).__name__, exc)
            return [], []
        duration_ms = int((time.monotonic() - started) * 1000)

        message = envelope.get("message") if isinstance(envelope, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        response_sha = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
        tokens_in = int(envelope.get("prompt_eval_count", 0)) if isinstance(envelope, dict) else 0
        tokens_out = int(envelope.get("eval_count", 0)) if isinstance(envelope, dict) else 0

        logger.info(
            "ollama response duration_ms=%d tokens_in=%d tokens_out=%d response_chars=%d",
            duration_ms,
            tokens_in,
            tokens_out,
            len(content or ""),
        )
        logger.debug("ollama response sha=%s\nFULL RESPONSE:\n%s", response_sha[:8], content or "")

        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=seed is not None,
            system_fingerprint=None,
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )

        if not isinstance(content, str):
            logger.warning(
                "ollama envelope missing message.content (will subdivide); got "
                "envelope keys: %r",
                list(envelope.keys()) if isinstance(envelope, dict) else type(envelope),
            )
            return [], []
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "ollama returned non-JSON content despite format=json (will "
                "subdivide; %s). First 500 chars: %r",
                exc,
                content[:500],
            )
            return [], []
        if not isinstance(payload, dict):
            logger.warning(
                "ollama JSON payload is not an object (will subdivide; got %s)",
                type(payload).__name__,
            )
            return [], []

        source = "instruction" if not llm_category_ids else "llm"
        return parse_findings_payload(payload, pages, source=source), [chunk]
