# Instruction Decomposition for Small Ollama Models — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Ollama runs with `--instruct`, decompose multi-rule instructions into atomic sub-rules and dispatch one Ollama call per rule per page-batch, so small local models (e.g. `llama3.1:8b`) can handle each rule reliably.

**Architecture:** A new `core/instruction_decomposer.py` module owns the parser + LLM-fallback logic. `cli/run.py` calls it once per session before the chunker, gated on `provider == "ollama"`. The chunker's existing per-batch submissions builder (project 1) is extended in two lines to emit one `_Submission` per atomic rule. The Anthropic path is untouched.

**Tech Stack:** Python 3.12, `httpx` (existing Ollama client), `pytest`, `dataclasses`, `re`. Reuses project 1's `_Submission`, `BatchSummary`, and `ThreadPoolExecutor` from `core/chunking.py`.

**Spec:** `docs/superpowers/specs/2026-05-09-instruction-decomposition-design.md`

---

## File Structure

**Create:**
- `src/kuroi/core/instruction_decomposer.py` — parser + LLM fallback logic, single responsibility (text decomposition)
- `tests/core/test_instruction_decomposer.py` — parser and decomposer unit tests
- `tests/cli/test_run_decompose.py` — end-to-end CLI integration

**Modify:**
- `src/kuroi/core/chunking.py` — submission-builder loop emits one `_Submission` per rule
- `src/kuroi/cli/run.py` — gate, decompose call, audit event, console line

No changes to providers (Anthropic, Ollama), audit_records, pricing, or rules.

---

## Task 1: Parser — pure splitting on numbering, bullets, paragraphs

The deterministic half of the decomposer. Pure function, no I/O. Unblocks every other task.

**Files:**
- Create: `src/kuroi/core/instruction_decomposer.py`
- Test: `tests/core/test_instruction_decomposer.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `/home/dev/Repositories/kuroi/tests/core/test_instruction_decomposer.py`:

```python
"""Tests for the instruction decomposer module."""

from kuroi.core.instruction_decomposer import parse_instruction


def test_parser_splits_numbered_list() -> None:
    rules = parse_instruction("1. Redact emails\n2. Redact phone numbers")
    assert rules == ("1. Redact emails", "2. Redact phone numbers")


def test_parser_splits_bulleted_list_with_dash() -> None:
    rules = parse_instruction("- Redact emails\n- Redact phone numbers")
    assert rules == ("- Redact emails", "- Redact phone numbers")


def test_parser_splits_bulleted_list_with_asterisk() -> None:
    rules = parse_instruction("* Redact emails\n* Redact phone numbers")
    assert rules == ("* Redact emails", "* Redact phone numbers")


def test_parser_splits_blank_line_paragraphs() -> None:
    rules = parse_instruction(
        "Redact all email addresses in the document.\n\nAlso redact phone numbers."
    )
    assert rules == (
        "Redact all email addresses in the document.",
        "Also redact phone numbers.",
    )


def test_parser_returns_single_rule_for_prose() -> None:
    rules = parse_instruction("Redact all PII")
    assert rules == ("Redact all PII",)


def test_parser_ignores_inline_numbers() -> None:
    """Inline numbers like phone digits or ZIPs must not trigger split.
    Anchor `^\\d+\\.` to line start prevents this."""
    rules = parse_instruction(
        "Redact ZIPs like 12345 and phone numbers like 555-1234 too."
    )
    assert rules == ("Redact ZIPs like 12345 and phone numbers like 555-1234 too.",)


def test_parser_strips_whitespace_around_rules() -> None:
    rules = parse_instruction("  1. foo  \n  2. bar  ")
    assert rules == ("1. foo", "2. bar")


def test_parser_drops_empty_rules() -> None:
    """A `2.` line with no content should be filtered out, not produce an empty rule."""
    rules = parse_instruction("1. foo\n2. \n3. bar")
    assert rules == ("1. foo", "3. bar")


def test_parser_numbering_takes_precedence_over_bullets() -> None:
    """If both numbered and bulleted markers appear, numbered split wins."""
    rules = parse_instruction("1. First numbered\n- bullet inside\n2. Second numbered")
    assert len(rules) == 2
    assert rules[0].startswith("1.")
    assert rules[1].startswith("2.")


def test_parser_handles_empty_input() -> None:
    """Empty or whitespace-only input returns a one-element tuple of empty string.
    The CLI gate prevents this case in practice; defensive fallback only."""
    assert parse_instruction("") == ("",)
    assert parse_instruction("   \n  ") == ("",)


def test_parser_handles_real_world_pacer_example() -> None:
    """Smoke test against the user's actual instruction shape."""
    instruction = (
        "1. All URLs (because some lead to bad sites). "
        "Redact the entire 'links' column.\n"
        "2. The names of the complainants in the 'content' column.\n"
        "3. The email addresses in the 'email' column.\n"
        "4. The IP addresses of the complainant.\n"
        "5. The 'lastreplier' column."
    )
    rules = parse_instruction(instruction)
    assert len(rules) == 5
    assert rules[0].startswith("1.")
    assert rules[4].startswith("5.")
```

- [ ] **Step 2: Run test to verify failure**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_instruction_decomposer.py -v
```

Expected: ImportError — `parse_instruction` doesn't exist.

- [ ] **Step 3: Implement the parser**

Create `/home/dev/Repositories/kuroi/src/kuroi/core/instruction_decomposer.py`:

```python
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

import re

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

    for matcher in (_NUMBERED_PREFIX, _BULLETED_PREFIX):
        rules = _split_by_line_prefix(stripped, matcher)
        if len(rules) >= 2:
            return rules

    paragraphs = tuple(p.strip() for p in _PARAGRAPH_BREAK.split(stripped))
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
        if rule:
            rules.append(rule)
    return tuple(rules) if len(rules) >= 2 else ()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_instruction_decomposer.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest
```

Expected: all green (375 baseline + 11 new = 386).

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/instruction_decomposer.py tests/core/test_instruction_decomposer.py
git commit -m "$(cat <<'EOF'
feat(decompose): add parse_instruction deterministic splitter

Pure function that splits multi-rule --instruct strings on numbering
(1., 2., ...), bullets (-, *), or blank-line-separated paragraphs.
Inline numbers (phone digits, ZIPs) don't trigger split because the
pattern is anchored to line start. Returns (instruction,) when no
structure is detected.

This is the first half of the instruction decomposer for project 2;
the LLM-fallback half lands in the next change.
EOF
)"
```

---

## Task 2: Decomposer skeleton + threshold gate (parser-only path)

Add `DecompositionResult` and `decompose()` with the parser path wired up. The LLM fallback is stubbed to "return original" for now; the next task plumbs in the actual call. Keeps each commit focused.

**Files:**
- Modify: `src/kuroi/core/instruction_decomposer.py`
- Test: `tests/core/test_instruction_decomposer.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `/home/dev/Repositories/kuroi/tests/core/test_instruction_decomposer.py`:

```python
from kuroi.core.instruction_decomposer import (
    DecompositionResult,
    LLM_FALLBACK_THRESHOLD_CHARS,
    decompose,
)


def test_decomposition_result_default_shape() -> None:
    """Type contract: rules is a tuple, source is one of the three labels,
    detail is a string."""
    r = DecompositionResult(rules=("a",), source="original", detail="x")
    assert r.rules == ("a",)
    assert r.source == "original"
    assert r.detail == "x"


def test_decompose_returns_parser_result_when_multi_rule(monkeypatch) -> None:
    """When the parser splits the input into >=2 rules, decompose returns
    those rules without invoking any LLM fallback."""
    # No provider needed — the parser short-circuits before any LLM call.
    result = decompose(
        "1. Redact emails\n2. Redact phones",
        provider=_MockOllamaProvider(),
    )
    assert result.rules == ("1. Redact emails", "2. Redact phones")
    assert result.source == "parser"
    assert "2 rules" in result.detail or "two" in result.detail.lower() or "split" in result.detail.lower()


def test_decompose_short_single_rule_skips_fallback() -> None:
    """Below the threshold, a single-rule parse is left alone — short
    instructions aren't worth the LLM round-trip."""
    short = "Redact all PII"  # well under 300 chars
    assert len(short) < LLM_FALLBACK_THRESHOLD_CHARS

    provider = _MockOllamaProvider()
    result = decompose(short, provider=provider)

    assert result.rules == (short,)
    assert result.source == "original"
    # Crucial: no HTTP call attempted.
    assert provider._client.post.call_count == 0


def test_decompose_threshold_boundary_at_300() -> None:
    """Single-rule instruction of length exactly 300 — fallback does NOT run.
    Uses `>` not `>=` so 300 stays in the original-pass-through bucket."""
    boundary_input = "x" * 300  # single-rule prose, no structure
    assert len(boundary_input) == LLM_FALLBACK_THRESHOLD_CHARS

    provider = _MockOllamaProvider()
    result = decompose(boundary_input, provider=provider)

    assert result.source == "original"
    assert provider._client.post.call_count == 0
```

Also add the `_MockOllamaProvider` helper near the top of the file (before the first test that uses it). Place it after the existing imports, before `def test_parser_*`:

```python
from unittest.mock import MagicMock


class _MockOllamaProvider:
    """A minimal Ollama-shaped stub for decomposer tests.

    Exposes _client (httpx.Client surrogate), _url, and model — the three
    fields the decomposer reads. Tests can configure _client.post to
    return canned responses or raise.
    """

    name = "ollama"
    model = "llama3.1:8b"

    def __init__(self) -> None:
        self._client = MagicMock()
        self._url = "http://localhost:11434"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_instruction_decomposer.py -v
```

Expected: ImportError on `DecompositionResult`, `LLM_FALLBACK_THRESHOLD_CHARS`, `decompose`.

- [ ] **Step 3: Add the dataclass, threshold, and skeleton decompose function**

Append to `/home/dev/Repositories/kuroi/src/kuroi/core/instruction_decomposer.py`:

```python
from dataclasses import dataclass
from typing import Any, Literal

LLM_FALLBACK_THRESHOLD_CHARS = 300
"""Below this length, a single-rule parse is left alone — short
instructions are unlikely to benefit from LLM splitting and aren't
worth the round-trip.
"""


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
    any failure mode collapses to (instruction,) with source set to
    "llm_fallback" or "original".

    Never raises.
    """
    parser_rules = parse_instruction(instruction)
    if len(parser_rules) >= 2:
        return DecompositionResult(
            rules=parser_rules,
            source="parser",
            detail=f"parser split into {len(parser_rules)} rules",
        )

    # Parser returned 1 rule. Decide whether to fall back to the LLM.
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

    # LLM fallback path lands in the next task. For now, return original
    # so the threshold-skipped tests pass.
    return DecompositionResult(
        rules=parser_rules,
        source="original",
        detail="LLM fallback not yet wired up",
    )
```

- [ ] **Step 4: Run decomposer tests**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_instruction_decomposer.py -v
```

Expected: 15 passed (11 parser + 4 new decomposer).

- [ ] **Step 5: Run full suite**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/instruction_decomposer.py tests/core/test_instruction_decomposer.py
git commit -m "$(cat <<'EOF'
feat(decompose): add DecompositionResult + threshold gate

Adds the public decomposer surface — DecompositionResult dataclass,
LLM_FALLBACK_THRESHOLD_CHARS = 300 constant, and decompose() function.
The parser-only path is fully wired; the LLM fallback path is stubbed
to return the original instruction for now (next change wires it up).

Threshold uses `>` not `>=` so a 300-char single-rule input falls
through as `source="original"` with no HTTP attempt.
EOF
)"
```

---

## Task 3: LLM fallback for unstructured long instructions

Wire in the actual LLM fallback. The decomposer dispatches one POST to the Ollama daemon when the parser returns one rule and the input exceeds the threshold. All failure modes collapse cleanly.

**Files:**
- Modify: `src/kuroi/core/instruction_decomposer.py`
- Test: `tests/core/test_instruction_decomposer.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `/home/dev/Repositories/kuroi/tests/core/test_instruction_decomposer.py`:

```python
import json
from unittest.mock import MagicMock

import httpx


def _llm_split_response(rules: list[str]) -> MagicMock:
    """Build a MagicMock httpx.Response that returns Ollama's chat envelope
    with `{"rules": [...]}` as the model's content."""
    response = MagicMock()
    response.json.return_value = {
        "message": {"content": json.dumps({"rules": rules})},
    }
    response.raise_for_status.return_value = None
    return response


def _long_unstructured(n: int = 600) -> str:
    """A single-paragraph string with no numbering / bullets / blank lines,
    long enough to exceed LLM_FALLBACK_THRESHOLD_CHARS."""
    return "Please redact every kind of personally identifiable information " * 8


def test_decompose_llm_fallback_runs_when_long_unstructured() -> None:
    long_instruction = _long_unstructured()
    assert len(long_instruction) > LLM_FALLBACK_THRESHOLD_CHARS

    provider = _MockOllamaProvider()
    provider._client.post.return_value = _llm_split_response(
        ["Redact names", "Redact emails", "Redact phone numbers"]
    )

    result = decompose(long_instruction, provider=provider)

    assert provider._client.post.call_count == 1
    assert result.source == "llm_fallback"
    assert result.rules == ("Redact names", "Redact emails", "Redact phone numbers")
    assert "3" in result.detail


def test_decompose_llm_fallback_posts_to_ollama_chat_endpoint() -> None:
    """The fallback should POST to <ollama_url>/api/chat with format=json
    and model=<provider.model>."""
    provider = _MockOllamaProvider()
    provider._client.post.return_value = _llm_split_response(["A", "B"])

    decompose(_long_unstructured(), provider=provider)

    args, kwargs = provider._client.post.call_args
    # First positional arg should be the chat endpoint URL.
    assert args[0] == "http://localhost:11434/api/chat"
    body = kwargs["json"]
    assert body["model"] == "llama3.1:8b"
    assert body["format"] == "json"
    assert body["stream"] is False
    # The user message should embed the original instruction text.
    user_msg = next(m for m in body["messages"] if m["role"] == "user")
    assert _long_unstructured() in user_msg["content"]


def test_decompose_falls_back_on_timeout() -> None:
    """A read-timeout from the daemon must not raise; original returned."""
    provider = _MockOllamaProvider()
    provider._client.post.side_effect = httpx.TimeoutException("timed out")

    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)  # original
    assert "timed out" in result.detail.lower() or "timeout" in result.detail.lower()


def test_decompose_falls_back_on_http_error() -> None:
    provider = _MockOllamaProvider()
    response = MagicMock()
    response.status_code = 500
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=response
    )
    provider._client.post.return_value = response

    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)
    assert "http" in result.detail.lower() or "500" in result.detail


def test_decompose_falls_back_on_malformed_json() -> None:
    """Model returned text that isn't valid JSON for the rules envelope."""
    provider = _MockOllamaProvider()
    response = MagicMock()
    response.json.return_value = {"message": {"content": "not json {{{"}}
    response.raise_for_status.return_value = None
    provider._client.post.return_value = response

    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)
    assert "json" in result.detail.lower() or "parse" in result.detail.lower()


def test_decompose_falls_back_on_missing_rules_key() -> None:
    """Model returned valid JSON but the wrong shape."""
    provider = _MockOllamaProvider()
    response = MagicMock()
    response.json.return_value = {"message": {"content": json.dumps({"foo": "bar"})}}
    response.raise_for_status.return_value = None
    provider._client.post.return_value = response

    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)
    assert "shape" in result.detail.lower() or "rules" in result.detail.lower()


def test_decompose_falls_back_on_single_rule_response() -> None:
    """Model only returned one rule — not a useful split. Use original."""
    provider = _MockOllamaProvider()
    provider._client.post.return_value = _llm_split_response(["only one"])

    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)
    assert "1" in result.detail or "one" in result.detail.lower()


def test_decompose_falls_back_on_all_empty_rules() -> None:
    """Model returned 2 rules but both empty/whitespace. Treat as <2 case."""
    provider = _MockOllamaProvider()
    provider._client.post.return_value = _llm_split_response(["  ", ""])

    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)


def test_decompose_uses_ollama_read_timeout() -> None:
    """The fallback's httpx.Timeout should match providers.ollama.READ_TIMEOUT_SECONDS."""
    from kuroi.providers.ollama import READ_TIMEOUT_SECONDS

    provider = _MockOllamaProvider()
    provider._client.post.return_value = _llm_split_response(["A", "B"])

    decompose(_long_unstructured(), provider=provider)

    kwargs = provider._client.post.call_args.kwargs
    timeout = kwargs.get("timeout")
    assert timeout is not None
    # httpx.Timeout exposes the read timeout as `.read`.
    assert timeout.read == READ_TIMEOUT_SECONDS
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_instruction_decomposer.py -k "llm_fallback or http_error or malformed or missing or single_rule or empty_rules or read_timeout or chat_endpoint" -v
```

Expected: most fail — the stub still returns `source="original"`.

- [ ] **Step 3: Implement the LLM fallback**

Replace the stubbed `decompose` body in `/home/dev/Repositories/kuroi/src/kuroi/core/instruction_decomposer.py` with a working LLM fallback. Add the imports at the top of the file:

```python
import json
import logging
from typing import Any, Literal

import httpx

from kuroi.providers.ollama import (
    CONNECT_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
)

logger = logging.getLogger("kuroi.core.instruction_decomposer")
```

Add the LLM-split system prompt as a module-level constant:

```python
_SPLIT_SYSTEM_PROMPT = (
    "You are a parser. Split the following redaction instruction into "
    "atomic rules. Return JSON only: {\"rules\": [\"rule 1\", \"rule 2\", ...]}. "
    "Each rule must be self-contained — a person reading just that rule "
    "should know what to redact. Do not add rules that aren't in the input."
)
```

Replace the `decompose` function body's last block (the "LLM fallback not yet wired up" return) with the actual fallback:

```python
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
        rules=(stripped,),
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
```

- [ ] **Step 4: Run the LLM fallback tests**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_instruction_decomposer.py -v
```

Expected: all 24 tests pass (11 parser + 4 threshold + 9 fallback).

- [ ] **Step 5: Run full suite**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/instruction_decomposer.py tests/core/test_instruction_decomposer.py
git commit -m "$(cat <<'EOF'
feat(decompose): add LLM fallback for long unstructured instructions

When the parser returns 1 rule and len(instruction) > 300 chars, the
decomposer dispatches a single Ollama "split this" call asking for
{"rules": [...]} JSON back. Reuses the Ollama provider's _client and
_url; uses providers.ollama.READ_TIMEOUT_SECONDS so the fallback
respects the same 120s budget as redaction calls.

Best-effort: timeouts, HTTP errors, malformed JSON, wrong shape, and
fewer-than-2-rule responses all collapse to the original instruction
with a descriptive `detail` string for the audit log. The decomposer
never raises.
EOF
)"
```

---

## Task 4: Chunker emits one submission per rule

Two-line change in the chunker's submission-builder so each instruction in the tuple becomes its own per-batch dispatch. Anthropic continues to receive a length-1 tuple (no decomposition); Ollama receives length-N after decomposition.

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Test: `tests/core/test_chunking_routing.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `/home/dev/Repositories/kuroi/tests/core/test_chunking_routing.py`:

```python
def test_per_rule_submissions_emitted_for_multi_rule_instructions() -> None:
    """When instructions tuple has length N>1, the chunker emits one
    _Submission per rule (each carrying instructions=(rule_k,))."""
    from kuroi.core.audit_records import ChunkRecord
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import DEFAULT_RETRY_POLICY
    from kuroi.core.pdf import Page, Word

    class _Recorder:
        name = "stub"
        model = "claude-opus-4-7"

        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

        def detect_redactions(
            self, pages, llm_category_ids, *, instructions=(),
            seed=None, attempt=0, layout_aware=False, model=None,
        ):
            self.calls.append((llm_category_ids, instructions))
            return [], [ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=1,
                tokens_out=1,
                duration_ms=1,
            )]

    pages = (Page(number=1, words=(Word(idx=0, text="x", bbox=(0, 0, 1, 1)),)),)
    provider = _Recorder()

    detect_redactions_chunked(
        provider,
        pages,
        (),                                      # no categories
        instructions=("rule A", "rule B", "rule C"),
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
    )

    # 3 rules → 3 calls per batch (1 batch). Each call carries one rule.
    assert len(provider.calls) == 3
    instr_tuples = [instr for cats, instr in provider.calls]
    assert ("rule A",) in instr_tuples
    assert ("rule B",) in instr_tuples
    assert ("rule C",) in instr_tuples
    # Categories empty on every call (instruction-only path).
    for cats, _instr in provider.calls:
        assert cats == ()


def test_single_rule_instructions_keeps_one_submission() -> None:
    """instructions=("only one",) → still 1 call per batch (today's shape)."""
    from kuroi.core.audit_records import ChunkRecord
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import DEFAULT_RETRY_POLICY
    from kuroi.core.pdf import Page, Word

    class _Recorder:
        name = "stub"
        model = "claude-opus-4-7"

        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def detect_redactions(
            self, pages, llm_category_ids, *, instructions=(),
            seed=None, attempt=0, layout_aware=False, model=None,
        ):
            self.calls.append(instructions)
            return [], [ChunkRecord(
                chunk_idx=0, pages=tuple(p.number for p in pages),
                temperature=0.0, seed_requested=None, seed_honored=False,
                system_fingerprint=None, prompt_sha256="a" * 64,
                response_sha256="b" * 64, tokens_in=1, tokens_out=1, duration_ms=1,
            )]

    pages = (Page(number=1, words=(Word(idx=0, text="x", bbox=(0, 0, 1, 1)),)),)
    provider = _Recorder()

    detect_redactions_chunked(
        provider,
        pages,
        (),
        instructions=("only one",),
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
    )

    assert len(provider.calls) == 1
    assert provider.calls[0] == ("only one",)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_chunking_routing.py::test_per_rule_submissions_emitted_for_multi_rule_instructions -v
```

Expected: FAIL — current chunker emits a single submission carrying the full instructions tuple, so `len(provider.calls) == 1`.

- [ ] **Step 3: Update the chunker submission builder**

In `/home/dev/Repositories/kuroi/src/kuroi/core/chunking.py`, find the submission-builder block in `detect_redactions_chunked`. The current code is:

```python
        if instructions:
            submissions.append(
                _Submission(
                    model=provider.model,
                    category_ids=(),
                    instructions=instructions,
                )
            )
```

Replace with:

```python
        for rule in instructions:
            submissions.append(
                _Submission(
                    model=provider.model,
                    category_ids=(),
                    instructions=(rule,),
                )
            )
```

Empty tuple → zero submissions appended (today's behavior). Length-1 → one submission with `instructions=(only_rule,)` — same prompt shape as today. Length-N → N submissions.

- [ ] **Step 4: Run the new chunker tests**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_chunking_routing.py -v
```

Expected: green (existing routing tests + 2 new instruction tests).

- [ ] **Step 5: Run full suite**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest
```

Expected: all green. Existing chunker / cli tests pass single-rule tuples that still produce one submission per batch — unchanged behavior.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking_routing.py
git commit -m "$(cat <<'EOF'
feat(chunking): one submission per --instruct rule per batch

The submission-builder iterates the instructions tuple and emits one
_Submission per element instead of bundling all rules into a single
submission. Length-1 tuples (current Anthropic behavior, current
single-rule Ollama behavior) produce exactly one submission as before;
length-N tuples (post-decomposition Ollama behavior) produce N.

This is the chunker side of project 2's instruction decomposition;
the CLI gate that calls the decomposer lands next.
EOF
)"
```

---

## Task 5: CLI integration — gate, decompose, audit, console line

Wire the decomposer into `cli/run.py`. Decompose runs once per session before the chunker, gated on `provider.name == "ollama" and instruct`. The result replaces `instruction_tuple`, an `instruction_decomposed` audit event is written (after audit.open), and a one-line summary is printed to the console.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Test: `tests/cli/test_run_decompose.py` (new)

- [ ] **Step 1: Read current run.py to find insertion points**

```bash
cd /home/dev/Repositories/kuroi && grep -n "instruction_tuple\|audit = AuditLog.open\|for chunk in chunks" src/kuroi/cli/run.py
```

Expected output includes:
- `instruction_tuple: tuple[str, ...] = (instruct,) if instruct else ()` (~line 265)
- `audit = AuditLog.open(...)` (~line 346)
- `for chunk in chunks: audit.write_event("chunk_request"...)` (~line 362)

These are the three reference points. The decomposer call goes near the `instruction_tuple` line; the audit event goes after `audit = AuditLog.open(...)` but before the chunk_request loop.

- [ ] **Step 2: Write the failing CLI tests**

Create `/home/dev/Repositories/kuroi/tests/cli/test_run_decompose.py`:

```python
"""End-to-end CLI tests for instruction decomposition (project 2).

These exercise the full kuroi run path with a stub provider, asserting:
- Ollama runs with multi-rule --instruct fire N submissions per batch
  and write an instruction_decomposed audit event.
- Anthropic runs with the same instruction fire 1 submission per batch
  and write NO instruction_decomposed event.
- Ollama runs with no --instruct don't call the decomposer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf
import pytest

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world", fontsize=11)
    p = tmp_path / "tiny.pdf"
    doc.save(str(p))
    doc.close()
    return p


class _FakeOllamaProvider:
    """Stub that mimics OllamaProvider's _client/_url/model surface AND
    its detect_redactions interface. Records every detect_redactions
    call; for the LLM split path, _client.post can be configured."""

    name = "ollama"
    model = "llama3.1:8b"

    def __init__(self) -> None:
        self._client = MagicMock()
        self._url = "http://localhost:11434"
        self.detect_calls: list[dict] = []

    def detect_redactions(
        self, pages, llm_category_ids, *, instructions=(),
        seed=None, attempt=0, layout_aware=False, model=None,
    ):
        self.detect_calls.append({
            "model": model,
            "categories": llm_category_ids,
            "instructions": instructions,
        })
        chunk = ChunkRecord(
            chunk_idx=0, pages=tuple(p.number for p in pages),
            temperature=0.0, seed_requested=seed, seed_honored=seed is not None,
            system_fingerprint=None, prompt_sha256="a" * 64,
            response_sha256="b" * 64, tokens_in=10, tokens_out=2, duration_ms=50,
        )
        return [], [chunk]


def _read_audit_events(audit_dir: Path) -> list[dict]:
    """Read the most recent audit JSONL and return every event as a dict."""
    files = sorted(audit_dir.glob("*.jsonl"))
    assert files, f"No audit JSONL in {audit_dir}"
    events = []
    for line in files[-1].read_text().splitlines():
        events.append(json.loads(line))
    return events


def test_ollama_multirule_instruct_decomposes_and_dispatches_per_rule(
    tiny_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5-numbered-rule --instruct on Ollama → 5 detect_redactions calls
    on the single-page batch + an instruction_decomposed audit event."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    from kuroi.cli.run import run

    fake = _FakeOllamaProvider()

    # Patch make_provider so kuroi gets our stub instead of a real OllamaProvider.
    import kuroi.cli.run as run_module
    monkeypatch.setattr(run_module, "make_provider", lambda *a, **kw: fake)

    instruct = (
        "1. Redact all email addresses.\n"
        "2. Redact all phone numbers.\n"
        "3. Redact all street addresses.\n"
        "4. Redact all person names.\n"
        "5. Redact all IP addresses."
    )
    out_path = tmp_path / "out.pdf"

    # Drive run() programmatically. We pass --provider ollama via the
    # config override path; check kuroi.cli.run's signature for the right
    # kwargs.
    with pytest.raises(SystemExit):  # No findings → typer.Exit(0)
        run(
            pdf=tiny_pdf,
            output=out_path,
            in_place=False,
            overwrite=True,
            rules="",
            instruct=instruct,
            yes=True,
            backup_dir=None,
            no_backup=True,
            audit_dir=tmp_path / "audit",
            provider="ollama",
            model="llama3.1:8b",
            ollama_url="http://localhost:11434",
            pages_per_batch=1,
            seed=None,
            verbose=0,
            quiet=False,
            layout_aware=False,
            max_retries=0,
        )

    # 1 page, 5 rules → 5 detect_redactions calls.
    assert len(fake.detect_calls) == 5
    instr_tuples = [c["instructions"] for c in fake.detect_calls]
    # Each call carries exactly one rule.
    for it in instr_tuples:
        assert len(it) == 1
    # All five rules were dispatched.
    flat = [it[0] for it in instr_tuples]
    assert any("email" in r for r in flat)
    assert any("phone" in r for r in flat)
    assert any("address" in r.lower() for r in flat)
    assert any("name" in r.lower() for r in flat)
    assert any("IP" in r for r in flat)

    # Audit log records an instruction_decomposed event.
    events = _read_audit_events(tmp_path / "audit")
    decomp_events = [e for e in events if e.get("event") == "instruction_decomposed"]
    assert len(decomp_events) == 1
    e = decomp_events[0]
    assert e["source"] == "parser"
    assert e["rule_count"] == 5
    assert len(e["rules"]) == 5


def test_anthropic_multirule_instruct_does_not_decompose(
    tiny_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same multi-rule instruct on Anthropic → 1 call per batch, no
    decomposition audit event."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    from kuroi.cli.run import run

    class _FakeAnthropicProvider:
        name = "anthropic"
        model = "claude-opus-4-7"

        def __init__(self) -> None:
            self.detect_calls: list[dict] = []

        def detect_redactions(self, pages, llm_category_ids, *, instructions=(),
                              seed=None, attempt=0, layout_aware=False, model=None):
            self.detect_calls.append({"instructions": instructions})
            return [], [ChunkRecord(
                chunk_idx=0, pages=tuple(p.number for p in pages),
                temperature=0.0, seed_requested=None, seed_honored=False,
                system_fingerprint=None, prompt_sha256="a" * 64,
                response_sha256="b" * 64, tokens_in=10, tokens_out=2, duration_ms=50,
            )]

    fake = _FakeAnthropicProvider()
    import kuroi.cli.run as run_module
    monkeypatch.setattr(run_module, "make_provider", lambda *a, **kw: fake)

    instruct = "1. Redact emails.\n2. Redact phones.\n3. Redact addresses."
    out_path = tmp_path / "out.pdf"

    with pytest.raises(SystemExit):
        run(
            pdf=tiny_pdf,
            output=out_path,
            in_place=False,
            overwrite=True,
            rules="",
            instruct=instruct,
            yes=True,
            backup_dir=None,
            no_backup=True,
            audit_dir=tmp_path / "audit",
            provider="anthropic",
            model="claude-opus-4-7",
            ollama_url=None,
            pages_per_batch=1,
            seed=None,
            verbose=0,
            quiet=False,
            layout_aware=False,
            max_retries=0,
        )

    # Anthropic path: 1 call with the full instruction tuple (length 1, the original).
    assert len(fake.detect_calls) == 1
    assert fake.detect_calls[0]["instructions"] == (instruct,)

    # No decomposition event.
    events = _read_audit_events(tmp_path / "audit")
    decomp_events = [e for e in events if e.get("event") == "instruction_decomposed"]
    assert decomp_events == []
```

Note: The CLI's `run` function may have a different exact set of kwargs than the snippet above — check `src/kuroi/cli/run.py` for the actual signature and adjust if needed. The function takes typer-wrapped args; pass each by keyword.

- [ ] **Step 3: Run to verify failure**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/cli/test_run_decompose.py -v
```

Expected: FAIL — both the multi-call assertion and the audit event lookup. The CLI doesn't decompose yet.

- [ ] **Step 4: Add the decomposition gate to `cli/run.py`**

Update `/home/dev/Repositories/kuroi/src/kuroi/cli/run.py`. Add the import at the top with the other `kuroi.core` imports:

```python
from kuroi.core.instruction_decomposer import DecompositionResult, decompose
```

Find the `instruction_tuple` line (~line 265):

```python
            instruction_tuple: tuple[str, ...] = (instruct,) if instruct else ()
```

Replace with:

```python
            instruction_tuple: tuple[str, ...] = (instruct,) if instruct else ()
            decomp_result: DecompositionResult | None = None
            if provider.name == "ollama" and instruct:
                decomp_result = decompose(instruct, provider)
                instruction_tuple = decomp_result.rules
                if len(decomp_result.rules) > 1:
                    console.print(
                        f"  Decomposed instruction into {len(decomp_result.rules)} "
                        f"atomic rules ({decomp_result.source})"
                    )
```

Now find the audit-open block (~line 346) followed by the chunk_request loop (~line 362):

```python
            audit = AuditLog.open(
                audit_path,
                ...
            )

            for chunk in chunks:
                audit.write_event("chunk_request", **asdict(chunk))
```

Insert the decomposition event write between them:

```python
            audit = AuditLog.open(
                audit_path,
                ...
            )

            if decomp_result is not None:
                audit.write_event(
                    "instruction_decomposed",
                    source=decomp_result.source,
                    detail=decomp_result.detail,
                    rule_count=len(decomp_result.rules),
                    rules=list(decomp_result.rules),
                )

            for chunk in chunks:
                audit.write_event("chunk_request", **asdict(chunk))
```

- [ ] **Step 5: Run the CLI tests**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/cli/test_run_decompose.py -v
```

Expected: green. (Both tests pass.)

If the test fails because of `run` kwargs mismatch, look at the actual `run()` signature in `src/kuroi/cli/run.py` and adjust the test call. Don't change the production code to fit the test.

- [ ] **Step 6: Run full suite**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest
```

Expected: all green. The existing CLI tests don't pass `--instruct` with multiple rules, or use the Anthropic provider (which is gated out), so they're unaffected.

- [ ] **Step 7: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run_decompose.py
git commit -m "$(cat <<'EOF'
feat(cli): decompose --instruct into per-rule submissions on Ollama

When the configured provider is Ollama and --instruct is set, the CLI
calls instruction_decomposer.decompose() once before the chunker.
Multi-rule instructions become a length-N tuple; the chunker
(post-Task-4) emits one Ollama call per rule per batch. An
instruction_decomposed audit event records source / detail / rule
count / rules list. Anthropic runs are unaffected — the gate is the
sole enabling check.
EOF
)"
```

---

## Task 6: Audit-event serialization regression test

A focused test that locks the JSONL line-shape for `instruction_decomposed`. Cheap insurance against future audit-record refactors silently dropping fields.

**Files:**
- Test: `tests/core/test_audit.py` (append; if missing, create it)

- [ ] **Step 1: Locate the existing audit test file (if any)**

```bash
cd /home/dev/Repositories/kuroi && ls tests/core/test_audit*.py
```

Likely already exists from project 1 (`test_audit_records.py`). If a `test_audit.py` exists, append there. If only `test_audit_records.py` exists, append there instead.

- [ ] **Step 2: Append the failing test**

Add to whichever audit-test file exists (assume `tests/core/test_audit_records.py` based on project-1 commits):

```python
def test_instruction_decomposed_event_serializes(tmp_path) -> None:
    """The instruction_decomposed event must round-trip through AuditLog
    with all four fields preserved (source, detail, rule_count, rules)."""
    import json

    from kuroi.core.audit import AuditLog

    audit_path = tmp_path / "test.jsonl"
    audit = AuditLog.open(
        audit_path,
        original=tmp_path / "in.pdf",
        output=tmp_path / "out.pdf",
        provider="ollama",
        model="llama3.1:8b",
        rules=(),
        session_id="abc",
        input_sha256="0" * 64,
        input_pages=1,
        input_bytes=100,
        model_version="llama3.1:8b",
        instructions=({"text": "Redact stuff"},),
        config_resolved_from=(),
    )
    audit.write_event(
        "instruction_decomposed",
        source="parser",
        detail="parser split into 5 rules",
        rule_count=5,
        rules=["1. A", "2. B", "3. C", "4. D", "5. E"],
    )
    audit.close(verification_passed=True, redaction_count=0, tokens_in=0,
               tokens_out=0, cost_usd=0.0, output_sha256="0" * 64)

    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    decomp = next(e for e in events if e.get("event") == "instruction_decomposed")
    assert decomp["source"] == "parser"
    assert decomp["detail"] == "parser split into 5 rules"
    assert decomp["rule_count"] == 5
    assert decomp["rules"] == ["1. A", "2. B", "3. C", "4. D", "5. E"]
```

(Adapt the `AuditLog.open` kwargs if the project-1 signature differs from above — use `git grep "AuditLog.open" src/kuroi/cli/run.py` for the canonical call.)

- [ ] **Step 3: Run to verify**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest tests/core/test_audit_records.py -k "instruction_decomposed_event" -v
```

Expected: green. This test exercises the existing `AuditLog.write_event` plumbing, which already supports arbitrary kwargs — no production code change needed.

- [ ] **Step 4: Run full suite**

```bash
cd /home/dev/Repositories/kuroi && source venv/bin/activate && python -m pytest
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add tests/core/test_audit_records.py
git commit -m "$(cat <<'EOF'
test(audit): lock instruction_decomposed event JSONL shape

Round-trip test asserts the four fields (source, detail, rule_count,
rules) survive AuditLog write+read. Cheap regression guard against
future audit-record refactors.
EOF
)"
```

---

## Self-Review

Spec coverage:

| Spec section | Plan task |
|---|---|
| Parser stages (numbered / bulleted / blank-line) | Task 1 |
| `LLM_FALLBACK_THRESHOLD_CHARS = 300` | Task 2 |
| `DecompositionResult` dataclass | Task 2 |
| `decompose()` parser-only path + threshold gate | Task 2 |
| `decompose()` LLM fallback (success + 7 failure modes) | Task 3 |
| `_llm_split` uses `READ_TIMEOUT_SECONDS` from `providers.ollama` | Task 3 |
| Chunker per-rule submission emission | Task 4 |
| CLI gate (`provider.name == "ollama" and instruct`) | Task 5 |
| `instruction_decomposed` audit event written after AuditLog.open | Task 5 |
| Console line for decomposed runs | Task 5 |
| Anthropic path unchanged | Task 5 (test asserts) |
| Audit JSONL forward compat | Task 6 |
| Best-effort fallback (never raises) | Task 3 |
| Empty-rules filter | Task 1 + Task 3 |
| Inline-numbers don't trigger split | Task 1 |
| Threshold boundary at 300 (uses `>`) | Task 2 |

Placeholder scan: no "TBD"/"TODO"/"add appropriate". Every code block contains real, runnable code (or the actual existing code with the diff highlighted).

Type consistency: `DecompositionResult` defined in Task 2 with fields `rules: tuple[str, ...]`, `source: Literal["original", "parser", "llm_fallback"]`, `detail: str` — used identically in Tasks 3 and 5. `_Submission` from project 1 (`model`, `category_ids`, `instructions`) is consumed in Task 4. `LLM_FALLBACK_THRESHOLD_CHARS` is the constant name used everywhere.

The plan is complete.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-09-instruction-decomposition.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
