"""Compute and represent a per-page diff between an original and a redacted PDF.

The diff is structural (word-level bbox comparison), not audit-log-driven.
This means kuroi diff works on any redacted PDF, not just kuroi's own output.

The output `Diff` is renderer-agnostic; cli/diff.py turns it into text, JSON, or HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class DiffRedaction:
    """A single word-region present in the original but absent in the redacted output."""

    bbox: tuple[float, float, float, float]
    before_text: str


@dataclass(frozen=True)
class DiffPage:
    page_number: int
    before_text: str
    after_text: str
    redactions: tuple[DiffRedaction, ...]


@dataclass(frozen=True)
class Diff:
    pages: tuple[DiffPage, ...]


def compute_diff(original: Path, redacted: Path) -> Diff:
    """Compare `original` and `redacted` page-by-page; return a `Diff`."""
    orig_doc = pymupdf.open(str(original))  # type: ignore[no-untyped-call]
    red_doc = pymupdf.open(str(redacted))  # type: ignore[no-untyped-call]
    try:
        if len(orig_doc) != len(red_doc):
            raise ValueError(f"page count differs: {len(orig_doc)} vs {len(red_doc)}")
        pages: list[DiffPage] = []
        for idx in range(len(orig_doc)):
            orig_page = orig_doc[idx]
            red_page = red_doc[idx]
            orig_words = orig_page.get_text("words")  # type: ignore[no-untyped-call]
            red_words = red_page.get_text("words")  # type: ignore[no-untyped-call]
            red_word_set = {(round(w[0], 1), round(w[1], 1), w[4]) for w in red_words}
            redactions: list[DiffRedaction] = []
            for x0, y0, x1, y1, word, *_ in orig_words:
                key = (round(x0, 1), round(y0, 1), word)
                if key not in red_word_set:
                    redactions.append(DiffRedaction(bbox=(x0, y0, x1, y1), before_text=word))
            pages.append(
                DiffPage(
                    page_number=idx + 1,
                    before_text=orig_page.get_text("text"),  # type: ignore[no-untyped-call]
                    after_text=red_page.get_text("text"),  # type: ignore[no-untyped-call]
                    redactions=tuple(redactions),
                )
            )
        return Diff(pages=tuple(pages))
    finally:
        orig_doc.close()  # type: ignore[no-untyped-call]
        red_doc.close()  # type: ignore[no-untyped-call]
