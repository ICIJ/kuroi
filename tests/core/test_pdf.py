from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from kuroi.core.pdf import ExtractionResult, OcrRequiredError, extract_word_index, serialize_for_llm


def test_extract_word_index_returns_words_with_bboxes(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world"])

    result = extract_word_index(pdf)
    pages = result.pages

    assert len(pages) == 1
    page = pages[0]
    assert page.number == 1
    assert len(page.words) == 2
    assert page.words[0].text == "Hello"
    assert page.words[0].idx == 0
    assert page.words[1].text == "world"
    assert page.words[1].idx == 1
    # bboxes are positive-area rectangles
    for w in page.words:
        x0, y0, x1, y1 = w.bbox
        assert x1 > x0 and y1 > y0


def test_serialize_for_llm_emits_numbered_tokens(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world", "Second page"])
    result = extract_word_index(pdf)
    pages = result.pages

    text = serialize_for_llm(pages)

    assert '<page n="1">' in text
    assert '<page n="2">' in text
    assert "[0]Hello" in text
    assert "[1]world" in text
    assert "[0]Second" in text
    assert "[1]page" in text


def test_extract_word_index_returns_extraction_result(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world"])

    result = extract_word_index(pdf)

    assert isinstance(result, ExtractionResult)
    assert result.ocr_page_count == 0
    assert len(result.pages) == 1
    assert result.pages[0].number == 1
    assert result.pages[0].words[0].text == "Hello"
    assert result.pages[0].words[1].text == "world"


def test_extract_word_index_raises_ocr_required_when_tesseract_missing(
    make_image_only_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_image_only_pdf
    monkeypatch.setattr("kuroi.core.pdf.shutil.which", lambda name: None)

    with pytest.raises(OcrRequiredError) as exc_info:
        extract_word_index(pdf)

    assert exc_info.value.page_numbers == (1,)


def test_extract_word_index_skips_blank_pages(make_pdf: Callable[..., Path]) -> None:
    # A page with zero words AND zero images is blank — not a scan candidate.
    pdf = make_pdf([""])  # insert_text with empty string → no words, no images

    result = extract_word_index(pdf)

    assert result.ocr_page_count == 0
    assert len(result.pages) == 1
    assert result.pages[0].words == ()


def test_extract_word_index_ocrs_image_pages(
    make_image_only_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = make_image_only_pdf
    monkeypatch.setattr(
        "kuroi.core.pdf.shutil.which",
        lambda name: "/usr/bin/tesseract" if name == "tesseract" else None,
    )
    # Stub _ocr_page_words to return one known word without shelling out to tesseract.
    monkeypatch.setattr(
        "kuroi.core.pdf._ocr_page_words",
        lambda page: [(10.0, 20.0, 50.0, 30.0, "redacted", 0, 0, 0)],
    )

    result = extract_word_index(pdf)

    assert result.ocr_page_count == 1
    assert len(result.pages) == 1
    assert result.pages[0].words[0].text == "redacted"
    assert result.pages[0].words[0].idx == 0
    assert result.pages[0].words[0].bbox == (10.0, 20.0, 50.0, 30.0)


def test_extract_word_index_ocrs_pages_with_text_and_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page with native text (e.g. a court-stamped header) AND an embedded
    image (e.g. an email screenshot) must still be OCR'd — the image content is
    invisible to native text extraction and would otherwise be missed."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Case header text", fontsize=11)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 128, 128))
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 200, 400, 500), pixmap=pix)
    pdf = tmp_path / "header_plus_image.pdf"
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(
        "kuroi.core.pdf.shutil.which",
        lambda name: "/usr/bin/tesseract" if name == "tesseract" else None,
    )
    monkeypatch.setattr(
        "kuroi.core.pdf._ocr_page_words",
        lambda page: [(10.0, 20.0, 50.0, 30.0, "ocred", 0, 0, 0)],
    )

    result = extract_word_index(pdf)

    assert result.ocr_page_count == 1
    assert len(result.pages) == 1
    assert result.pages[0].words[0].text == "ocred"
