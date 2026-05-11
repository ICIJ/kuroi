"""Tests for the instruction decomposer module."""

import json
from unittest.mock import MagicMock

import httpx

from kuroi.core.instruction_decomposer import (
    LLM_FALLBACK_THRESHOLD_CHARS,
    DecompositionResult,
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
    rules = parse_instruction("Redact ZIPs like 12345 and phone numbers like 555-1234 too.")
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
    assert (
        "2" in result.detail or "two" in result.detail.lower() or "split" in result.detail.lower()
    )


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


def test_decompose_falls_back_on_non_json_http_body() -> None:
    """The Ollama daemon returns a non-JSON body (e.g. a proxy error page).
    response.json() raises JSONDecodeError, which must be caught — not
    propagate out of decompose()."""
    provider = _MockOllamaProvider()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
    provider._client.post.return_value = response

    # Must not raise.
    result = decompose(_long_unstructured(), provider=provider)

    assert result.source == "llm_fallback"
    assert result.rules == (_long_unstructured(),)
    assert "json" in result.detail.lower()
