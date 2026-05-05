"""Ollama provider — native HTTP to a local or remote Ollama daemon."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers._shared import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_findings_payload,
)

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 120.0


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
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        if not llm_category_ids and not instructions:
            return [], []
        user_prompt = build_user_prompt(pages, llm_category_ids, instructions)
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }

        started = time.monotonic()
        try:
            response = self._client.post(f"{self._url}/api/chat", json=body)
            response.raise_for_status()
            envelope = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return [], []
        duration_ms = int((time.monotonic() - started) * 1000)

        message = envelope.get("message") if isinstance(envelope, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        response_sha = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
        tokens_in = int(envelope.get("prompt_eval_count", 0)) if isinstance(envelope, dict) else 0
        tokens_out = int(envelope.get("eval_count", 0)) if isinstance(envelope, dict) else 0

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
            return [], [chunk]
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return [], [chunk]
        if not isinstance(payload, dict):
            return [], [chunk]

        source = "instruction" if not llm_category_ids else "llm"
        return parse_findings_payload(payload, pages, source=source), [chunk]
