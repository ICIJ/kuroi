"""PDF text extraction in the word-index protocol used throughout kuroi.

The protocol: every word on every page is assigned a stable per-page index. The
LLM sees `[idx]token` markers and returns `(page, start, end)` spans, which we
map back to bounding-box unions for redaction.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class OcrRequiredError(Exception):
    def __init__(self, page_numbers: tuple[int, ...]) -> None:
        pages_str = ", ".join(str(p) for p in page_numbers)
        super().__init__(f"OCR required for image-only pages: {pages_str}")
        self.page_numbers = page_numbers


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[Page, ...]
    ocr_page_count: int  # 0 if no OCR was needed


def _ocr_page_words(page: pymupdf.Page) -> list[Any]:  # type: ignore[type-arg]
    tp = page.get_textpage_ocr(full=True, language="eng", dpi=300)  # type: ignore[no-untyped-call]
    return page.get_text("words", textpage=tp)  # type: ignore[no-untyped-call]


def extract_word_index(pdf_path: Path) -> ExtractionResult:
    """Extract every word on every page with its bounding box.

    Any page with at least one embedded image is treated as a scan candidate
    so OCR can recover text drawn inside the image — including the common case
    where a court-stamped header sits on top of a screenshot exhibit. If any
    scan candidates are found and tesseract is not in PATH, raises
    OcrRequiredError. Otherwise OCRs scan candidates via PyMuPDF's tesseract
    bridge; the OCR pass replaces the page's word list, since `full=True` OCR
    re-extracts the rendered native text alongside the image content. Returns
    an ExtractionResult whose ocr_page_count reflects how many pages were OCR'd.
    """
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    try:
        pages: list[Page] = []
        scan_candidates: list[int] = []  # 1-indexed page numbers

        for page_idx in range(doc.page_count):
            pdf_page = doc[page_idx]
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
            if pdf_page.get_images():  # type: ignore[no-untyped-call]
                scan_candidates.append(page_idx + 1)

        if scan_candidates:
            if shutil.which("tesseract") is None:
                raise OcrRequiredError(tuple(scan_candidates))
            for page_1idx in scan_candidates:
                pdf_page = doc[page_1idx - 1]
                raw = _ocr_page_words(pdf_page)
                words = tuple(
                    Word(
                        idx=i,
                        text=str(w[4]),
                        bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
                    )
                    for i, w in enumerate(raw)
                )
                pages[page_1idx - 1] = Page(number=page_1idx, words=words)

        return ExtractionResult(pages=tuple(pages), ocr_page_count=len(scan_candidates))
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
