"""Decompose multi-rule --instruct strings into atomic sub-rules.

Used on Ollama runs to dispatch one provider call per rule (small local
models can handle one rule reliably but choke on multi-rule prompts).
The Anthropic path never calls into this module; the gate is in
cli/run.py.

Two stages:
1. parse_instruction() — pure deterministic splitter on numbering,
   bullets, or blank-line-separated paragraphs.
2. decompose() — runs the parser; when it returns one rule and the
   input is long enough, dispatches a single LLM "split this" call to
   the same Ollama provider as a fallback. Best-effort: any failure
   collapses to the original instruction so runs never regress versus
   today.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from kuroi.providers.ollama import (
    CONNECT_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
)

logger = logging.getLogger("kuroi.core.instruction_decomposer")

_NUMBERED_PREFIX = re.compile(r"^\d+\.\s", re.MULTILINE)
_BULLETED_PREFIX = re.compile(r"^[-*]\s", re.MULTILINE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def parse_instruction(instruction: str) -> tuple[str, ...]:
    """Split an instruction into atomic rules using deterministic heuristics.

    Tries three strategies in precedence:
    1. Lines starting with `\\d+\\.` (numbered list).
    2. Lines starting with `-` or `*` (bulleted list).
    3. Two-or-more consecutive newlines (paragraph break).

    Each strategy is tried only if the previous didn't yield >=2 non-empty
    rules. If none yields >=2, returns (instruction,) trimmed.
    """
    stripped = instruction.strip()
    if not stripped:
        return ("",)

    # Normalize leading whitespace on each line to allow regex to match properly
    normalized = "\n".join(line.lstrip() for line in stripped.split("\n"))

    for matcher in (_NUMBERED_PREFIX, _BULLETED_PREFIX):
        rules = _split_by_line_prefix(normalized, matcher)
        if len(rules) >= 2:
            return rules

    paragraphs = tuple(p.strip() for p in _PARAGRAPH_BREAK.split(normalized))
    paragraphs = tuple(p for p in paragraphs if p)
    if len(paragraphs) >= 2:
        return paragraphs

    return (stripped,)


def _split_by_line_prefix(text: str, matcher: re.Pattern[str]) -> tuple[str, ...]:
    """Slice `text` at each line matching `matcher`. Strip and drop empties."""
    matches = list(matcher.finditer(text))
    if len(matches) < 2:
        return ()
    rules: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        rule = text[start:end].strip()
        # Drop rules that are only the marker (e.g., "2." or "- " with no content after)
        # Match marker patterns: "N. ", "- ", "* "
        marker_match = re.match(r"^(\d+\.|[-*])\s*$", rule)
        if not marker_match:  # Has content beyond just the marker
            rules.append(rule)
    return tuple(rules) if len(rules) >= 2 else ()


LLM_FALLBACK_THRESHOLD_CHARS = 300
"""Below this length, a single-rule parse is left alone — short
instructions are unlikely to benefit from LLM splitting and aren't
worth the round-trip.
"""

_SPLIT_SYSTEM_PROMPT = (
    "You are a parser. Split the following redaction instruction into "
    "atomic rules. Return JSON only: {\"rules\": [\"rule 1\", \"rule 2\", ...]}. "
    "Each rule must be self-contained — a person reading just that rule "
    "should know what to redact. Do not add rules that aren't in the input."
)


@dataclass(frozen=True)
class DecompositionResult:
    """Outcome of decomposing an instruction.

    rules: atomic sub-rules. Always at least one element. Equal to
        (instruction,) when no useful split was found.
    source: where the rules came from.
        "original"     — parser returned 1 rule and threshold gate
                         skipped LLM fallback (or input was empty).
        "parser"       — deterministic splitter found >=2 rules.
        "llm_fallback" — LLM split call was attempted (it may still
                         have failed; rules may equal (instruction,)
                         in that case; check `detail` for the reason).
    detail: human-readable note for the audit log.
    """

    rules: tuple[str, ...]
    source: Literal["original", "parser", "llm_fallback"]
    detail: str


def decompose(
    instruction: str,
    provider: Any,
    *,
    threshold_chars: int = LLM_FALLBACK_THRESHOLD_CHARS,
) -> DecompositionResult:
    """Decompose `instruction` into atomic rules.

    Parser first. If the parser returns 1 rule AND len(instruction) >
    threshold_chars, dispatches one LLM split call against `provider`
    (an Ollama provider exposing _client / _url / model). Best-effort:
    any failure mode collapses to (instruction,).

    Never raises.
    """
    parser_rules = parse_instruction(instruction)
    if len(parser_rules) >= 2:
        return DecompositionResult(
            rules=parser_rules,
            source="parser",
            detail=f"parser split into {len(parser_rules)} rules",
        )

    stripped = instruction.strip()
    if len(stripped) <= threshold_chars:
        return DecompositionResult(
            rules=parser_rules,
            source="original",
            detail=(
                f"single-rule input under {threshold_chars}-char threshold; "
                "no LLM fallback attempted"
            ),
        )

    rules, detail = _llm_split(instruction, provider)
    if rules is not None:
        return DecompositionResult(
            rules=rules,
            source="llm_fallback",
            detail=detail,
        )
    return DecompositionResult(
        rules=(instruction,),
        source="llm_fallback",
        detail=detail,
    )


def _llm_split(
    instruction: str,
    provider: Any,
) -> tuple[tuple[str, ...] | None, str]:
    """Dispatch one Ollama "split this" call. Return (rules, detail) where
    rules is None on any failure that should collapse to the original.

    Failure modes (network, parse, shape, count) are caught individually
    so the audit detail can be specific.
    """
    body = {
        "model": provider.model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": _SPLIT_SYSTEM_PROMPT},
            {"role": "user", "content": instruction},
        ],
    }
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=READ_TIMEOUT_SECONDS,
        write=READ_TIMEOUT_SECONDS,
        pool=READ_TIMEOUT_SECONDS,
    )
    try:
        response = provider._client.post(
            f"{provider._url}/api/chat", json=body, timeout=timeout
        )
        response.raise_for_status()
        envelope = response.json()
    except httpx.TimeoutException as exc:
        msg = f"llm fallback: timed out after {READ_TIMEOUT_SECONDS}s ({exc})"
        logger.warning(msg)
        return None, msg
    except httpx.HTTPStatusError as exc:
        msg = f"llm fallback: HTTP {exc.response.status_code}"
        logger.warning(msg)
        return None, msg
    except httpx.HTTPError as exc:
        msg = f"llm fallback: connection error ({exc})"
        logger.warning(msg)
        return None, msg
    except (json.JSONDecodeError, ValueError) as exc:
        msg = f"llm fallback: response body not valid JSON ({exc})"
        logger.warning(msg)
        return None, msg

    message = envelope.get("message") if isinstance(envelope, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        msg = "llm fallback: response missing message.content"
        logger.warning(msg)
        return None, msg

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        msg = "llm fallback: response not valid JSON"
        logger.warning(msg)
        return None, msg

    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        msg = "llm fallback: response shape invalid (no `rules` array)"
        logger.warning(msg)
        return None, msg

    rules = tuple(
        rule.strip()
        for rule in payload["rules"]
        if isinstance(rule, str) and rule.strip()
    )
    if len(rules) < 2:
        msg = f"llm fallback: only {len(rules)} valid rule(s) returned"
        logger.warning(msg)
        return None, msg

    return rules, f"llm split into {len(rules)} rules"
