"""PDF text extraction in the word-index protocol used throughout kuroi.

The protocol: every word on every page is assigned a stable per-page index. The
LLM sees `[idx]token` markers and returns `(page, start, end)` spans, which we
map back to bounding-box unions for redaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(frozen=True)
class Word:
    idx: int
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points


@dataclass(frozen=True)
class Page:
    number: int  # 1-indexed
    words: tuple[Word, ...]


def extract_word_index(pdf_path: Path) -> tuple[Page, ...]:
    """Extract every word on every page with its bounding box.

    Returns a tuple of Page objects in document order. Each Word has a stable
    per-page index used by the LLM to refer back to it.
    """
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    try:
        pages: list[Page] = []
        for page_idx in range(doc.page_count):
            pdf_page = doc[page_idx]
            # get_text("words") → tuple of (x0, y0, x1, y1, "word", block_no, line_no, word_no)
            raw = pdf_page.get_text("words")  # type: ignore[no-untyped-call]
            words = tuple(
                Word(
                    idx=i,
                    text=str(w[4]),
                    bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
                )
                for i, w in enumerate(raw)
            )
            pages.append(Page(number=page_idx + 1, words=words))
        return tuple(pages)
    finally:
        doc.close()  # type: ignore[no-untyped-call]


def serialize_for_llm(pages: tuple[Page, ...]) -> str:
    """Render the word index as the prompt-side representation.

    Output shape:
        <page n="1">
        [0]Hello [1]world
        </page>
        <page n="2">
        [0]Second [1]page
        </page>
    """
    chunks: list[str] = []
    for page in pages:
        body = " ".join(f"[{w.idx}]{w.text}" for w in page.words)
        chunks.append(f'<page n="{page.number}">\n{body}\n</page>')
    return "\n".join(chunks)
