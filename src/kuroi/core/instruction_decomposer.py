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
