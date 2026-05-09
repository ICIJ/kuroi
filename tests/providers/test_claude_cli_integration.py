"""Opt-in smoke test for ClaudeCliProvider — runs a real `claude` query.

Skipped automatically if the binary is not on PATH, and excluded from the
default `make test` via the `slow` marker.
"""

from __future__ import annotations

import shutil

import pytest

from kuroi.core.pdf import Page, Word
from kuroi.providers.claude_cli import ClaudeCliProvider


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not installed",
    ),
]


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_real_query_returns_at_least_one_chunk() -> None:
    provider = ClaudeCliProvider(model="claude-haiku-4-5-20251001", timeout_s=120)
    pages = (_page(1, ["My", "name", "is", "Sarah", "Chen", "."]),)
    findings, chunks = provider.detect_redactions(pages, ("person_name",))
    assert len(chunks) == 1
    # Findings may be 0 if the model is conservative, so we don't assert > 0.
