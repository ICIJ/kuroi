"""Tests for the selection-aware path of ``extract_word_index``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from kuroi.core.page_selection import parse
from kuroi.core.pdf import OcrRequiredError, extract_word_index


def test_extract_word_index_filters_to_selection(
    make_pdf: Callable[..., Path],
) -> None:
    pdf = make_pdf(["one", "two", "three", "four", "five"])
    selection = parse("2,4")

    result = extract_word_index(pdf, selection=selection)

    assert tuple(p.number for p in result.pages) == (2, 4)
    assert result.total_pages == 5
    assert result.ocr_page_count == 0


def test_extract_word_index_preserves_word_indices_under_selection(
    make_pdf: Callable[..., Path],
) -> None:
    # The Word.idx field is per-page-0-based regardless of which pages
    # were selected — the LLM protocol depends on this invariant.
    pdf = make_pdf(["alpha beta gamma", "delta epsilon"])
    selection = parse("2")

    result = extract_word_index(pdf, selection=selection)

    page = result.pages[0]
    assert page.number == 2
    assert tuple(w.idx for w in page.words) == (0, 1)
    assert tuple(w.text for w in page.words) == ("delta", "epsilon")


def test_extract_word_index_skips_ocr_check_on_unselected_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Build a 2-page PDF: page 1 native text, page 2 image-only.
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Hello world", fontsize=11)
    page2 = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 128, 128))
    pix.clear_with(200)
    page2.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    pdf = tmp_path / "mixed.pdf"
    doc.save(str(pdf))
    doc.close()

    # Tesseract is unavailable.
    monkeypatch.setattr("kuroi.core.pdf.shutil.which", lambda name: None)
    selection = parse("1")

    # No OcrRequiredError because page 2 (the scan candidate) was not selected.
    result = extract_word_index(pdf, selection=selection)

    assert tuple(p.number for p in result.pages) == (1,)
    assert result.ocr_page_count == 0
    assert result.total_pages == 2


def test_extract_word_index_raises_ocr_when_selected_page_needs_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same 2-page fixture as above, but select page 2 → must raise.
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Hello world", fontsize=11)
    page2 = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 128, 128))
    pix.clear_with(200)
    page2.insert_image(pymupdf.Rect(72, 72, 200, 200), pixmap=pix)
    pdf = tmp_path / "mixed.pdf"
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr("kuroi.core.pdf.shutil.which", lambda name: None)
    selection = parse("2")

    with pytest.raises(OcrRequiredError) as exc_info:
        extract_word_index(pdf, selection=selection)

    assert exc_info.value.page_numbers == (2,)


def test_extract_word_index_no_selection_unchanged_behavior(
    make_pdf: Callable[..., Path],
) -> None:
    # Without a selection, behavior matches the legacy contract.
    pdf = make_pdf(["one", "two", "three"])

    result = extract_word_index(pdf)

    assert tuple(p.number for p in result.pages) == (1, 2, 3)
    assert result.total_pages == 3
