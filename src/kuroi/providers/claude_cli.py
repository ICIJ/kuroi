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
import json
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


def _cli_not_found_class():
    from claude_agent_sdk import CLINotFoundError

    return CLINotFoundError


def _process_error_class():
    from claude_agent_sdk import ProcessError

    return ProcessError


def _cli_json_decode_error_class():
    from claude_agent_sdk import CLIJSONDecodeError

    return CLIJSONDecodeError


def _cli_connection_error_class():
    from claude_agent_sdk import CLIConnectionError

    return CLIConnectionError


_AUTH_FAILURE_PATTERNS = ("not authenticated", "no credentials", "please log in")


def _empty_chunk(
    pages: tuple[Page, ...],
    prompt_sha: str,
    seed: int | None,
) -> list[ChunkRecord]:
    """Audit chunk for a soft-failed call (no usable response text).

    Token counts and SHAs default to zero / the empty-string SHA, but the
    prompt SHA is preserved so a later replay can still re-run the same
    prompt.
    """
    return [
        ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=False,
            system_fingerprint=None,
            prompt_sha256=prompt_sha,
            response_sha256=hashlib.sha256(b"").hexdigest(),
            tokens_in=0,
            tokens_out=0,
            duration_ms=0,
        )
    ]


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

        effective_model = model or self.model
        static_prefix = build_user_static_prefix(llm_category_ids, instructions)
        document_block = build_user_document_block(pages, layout_aware=layout_aware)
        user_prompt = static_prefix + document_block
        prompt_sha = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
        system_prompt = build_system_prompt(layout_aware)

        import anyio  # local import keeps Provider Protocol pure

        started = time.monotonic()
        try:
            text, tokens_in, tokens_out = anyio.run(
                self._aexec, user_prompt, system_prompt, effective_model
            )
        except _cli_not_found_class() as exc:
            from kuroi.core.config import ConfigError  # local to avoid cycle

            raise ConfigError(
                "Claude CLI not found. Install with `pip install "
                "claude-agent-sdk` or `npm install -g "
                "@anthropic-ai/claude-code`, then run `claude /login` to "
                f"authenticate. (SDK said: {exc})"
            ) from exc
        except _process_error_class() as exc:
            from kuroi.core.config import ConfigError  # local to avoid cycle

            message = str(exc).lower()
            if any(p in message for p in _AUTH_FAILURE_PATTERNS):
                raise ConfigError(
                    "Claude CLI is not authenticated. Run `claude /login` "
                    "to log in with your subscription account. (SDK said: "
                    f"{exc})"
                ) from exc
            logger.warning(
                "claude-cli ProcessError (will subdivide): %s", exc
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
        except _cli_json_decode_error_class() as exc:
            logger.warning(
                "claude-cli CLIJSONDecodeError (will subdivide): %s", exc
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
        except _cli_connection_error_class() as exc:
            logger.warning(
                "claude-cli CLIConnectionError (will subdivide): %s", exc
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
        except TimeoutError as exc:
            logger.warning(
                "claude-cli timed out after %ds (will subdivide): %s",
                self._timeout_s,
                exc,
            )
            return [], _empty_chunk(pages, prompt_sha, seed)
        duration_ms = int((time.monotonic() - started) * 1000)
        response_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

        logger.info(
            "claude-cli response duration_ms=%d tokens_in=%d tokens_out=%d "
            "response_chars=%d",
            duration_ms,
            tokens_in,
            tokens_out,
            len(text),
        )

        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=False,
            system_fingerprint=None,
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
                "claude-cli returned non-JSON response (%s; will subdivide). "
                "First 500 chars: %r",
                exc,
                text[:500],
            )
            return [], [chunk]
        if not isinstance(payload, dict):
            logger.warning(
                "claude-cli payload is not an object (will subdivide; got %s)",
                type(payload).__name__,
            )
            return [], [chunk]

        source = "instruction" if not llm_category_ids else "llm"
        return parse_findings_payload(payload, pages, source=source), [chunk]

    async def _aexec(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
    ) -> tuple[str, int, int]:
        """Run one `query()` call and return (text, tokens_in, tokens_out)."""
        import anyio
        from claude_agent_sdk import (  # local import; SDK is heavy
            ClaudeAgentOptions,
        )
        from claude_agent_sdk import (
            query as sdk_query,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            max_turns=1,
            allowed_tools=[],
            permission_mode="default",
            setting_sources=[],
            cli_path=self._cli_path,
        )
        qfn = self._query_fn if self._query_fn is not None else sdk_query

        result_text = ""
        tokens_in = 0
        tokens_out = 0
        with anyio.fail_after(self._timeout_s):
            async for message in qfn(prompt=prompt, options=options):
                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        text_attr = getattr(block, "text", None)
                        if isinstance(text_attr, str):
                            result_text += text_attr
                usage = getattr(message, "usage", None)
                if usage is not None:
                    tokens_in = int(getattr(usage, "input_tokens", 0)) or tokens_in
                    tokens_out = int(getattr(usage, "output_tokens", 0)) or tokens_out
        return result_text, tokens_in, tokens_out
