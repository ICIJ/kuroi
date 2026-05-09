"""Tests for the instruction decomposer module."""

from unittest.mock import MagicMock

from kuroi.core.instruction_decomposer import (
    DecompositionResult,
    LLM_FALLBACK_THRESHOLD_CHARS,
    decompose,
    parse_instruction,
)


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


def test_decomposition_result_default_shape() -> None:
    """Type contract: rules is a tuple, source is one of the three labels,
    detail is a string."""
    r = DecompositionResult(rules=("a",), source="original", detail="x")
    assert r.rules == ("a",)
    assert r.source == "original"
    assert r.detail == "x"


def test_decompose_returns_parser_result_when_multi_rule() -> None:
    """When the parser splits the input into >=2 rules, decompose returns
    those rules without invoking any LLM fallback."""
    result = decompose(
        "1. Redact emails\n2. Redact phones",
        provider=_MockOllamaProvider(),
    )
    assert result.rules == ("1. Redact emails", "2. Redact phones")
    assert result.source == "parser"
    assert "2" in result.detail or "two" in result.detail.lower() or "split" in result.detail.lower()


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
