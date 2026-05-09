"""Tests for ClaudeCliProvider — async-bridged subprocess via claude-agent-sdk."""

from __future__ import annotations

from kuroi.core.pdf import Page, Word
from kuroi.providers.claude_cli import ClaudeCliProvider


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_provider_has_name_and_default_model() -> None:
    provider = ClaudeCliProvider()
    assert provider.name == "claude-cli"
    assert provider.model == "claude-opus-4-7"


def test_short_circuit_when_no_categories_and_no_instructions() -> None:
    provider = ClaudeCliProvider(query_fn=_must_not_be_called)
    pages = (_page(1, ["Hello"]),)
    findings, chunks = provider.detect_redactions(pages, llm_category_ids=())
    assert findings == []
    assert chunks == []


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("query_fn should not be called when there is no work")
