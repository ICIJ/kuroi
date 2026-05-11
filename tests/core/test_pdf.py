from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from kuroi.core.pdf import (
    ExtractionResult,
    OcrRequiredError,
    Page,
    extract_word_index,
    index_by_number,
    serialize_for_llm,
)


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


# ---------------------------------------------------------------------------
# slice_page tests
# ---------------------------------------------------------------------------


def test_slice_page_reindexes_words_to_zero_base() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = tuple(
        Word(idx=i, text=f"w{i}", bbox=(float(i), 0.0, float(i + 1), 1.0)) for i in range(10)
    )
    page = Page(number=7, words=words)

    sliced = slice_page(page, 3, 7)

    assert tuple(w.idx for w in sliced.words) == (0, 1, 2, 3)
    assert tuple(w.text for w in sliced.words) == ("w3", "w4", "w5", "w6")


def test_slice_page_preserves_page_number() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = tuple(Word(idx=i, text=f"w{i}", bbox=(0.0, 0.0, 1.0, 1.0)) for i in range(5))
    page = Page(number=42, words=words)

    sliced = slice_page(page, 1, 4)

    assert sliced.number == 42


def test_slice_page_preserves_word_text_and_bbox() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = (
        Word(idx=0, text="alpha", bbox=(0.0, 0.0, 5.0, 1.0)),
        Word(idx=1, text="beta", bbox=(5.0, 0.0, 10.0, 1.0)),
        Word(idx=2, text="gamma", bbox=(10.0, 0.0, 15.0, 1.0)),
    )
    page = Page(number=1, words=words)

    sliced = slice_page(page, 1, 3)

    assert sliced.words[0].text == "beta"
    assert sliced.words[0].bbox == (5.0, 0.0, 10.0, 1.0)
    assert sliced.words[1].text == "gamma"
    assert sliced.words[1].bbox == (10.0, 0.0, 15.0, 1.0)


def test_slice_page_full_range_round_trips_text() -> None:
    from kuroi.core.pdf import Page, Word, slice_page

    words = tuple(Word(idx=i, text=f"w{i}", bbox=(0.0, 0.0, 1.0, 1.0)) for i in range(3))
    page = Page(number=1, words=words)

    sliced = slice_page(page, 0, 3)

    assert tuple(w.text for w in sliced.words) == ("w0", "w1", "w2")
    assert tuple(w.idx for w in sliced.words) == (0, 1, 2)


def test_slice_page_rejects_empty_range() -> None:
    import pytest

    from kuroi.core.pdf import Page, Word, slice_page

    page = Page(number=1, words=(Word(idx=0, text="x", bbox=(0.0, 0.0, 1.0, 1.0)),))

    with pytest.raises(ValueError, match="out of range"):
        slice_page(page, 0, 0)


def test_slice_page_rejects_negative_start() -> None:
    import pytest

    from kuroi.core.pdf import Page, Word, slice_page

    page = Page(number=1, words=(Word(idx=0, text="x", bbox=(0.0, 0.0, 1.0, 1.0)),))

    with pytest.raises(ValueError, match="out of range"):
        slice_page(page, -1, 1)


def test_slice_page_rejects_end_past_word_count() -> None:
    import pytest

    from kuroi.core.pdf import Page, Word, slice_page

    page = Page(
        number=1,
        words=(
            Word(idx=0, text="a", bbox=(0.0, 0.0, 1.0, 1.0)),
            Word(idx=1, text="b", bbox=(0.0, 0.0, 1.0, 1.0)),
        ),
    )

    with pytest.raises(ValueError, match="out of range"):
        slice_page(page, 0, 3)


def test_word_has_block_id_default_zero() -> None:
    from kuroi.core.pdf import Word

    w = Word(idx=0, text="hello", bbox=(0.0, 0.0, 1.0, 1.0))

    assert w.block_id == 0


def test_extract_word_index_populates_block_id(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["First paragraph here.\n\nSecond paragraph here."])

    result = extract_word_index(pdf)
    page = result.pages[0]

    block_ids = {w.block_id for w in page.words}
    # Two visually-separated paragraphs produce at least two distinct blocks.
    assert len(block_ids) >= 2
    # Words sharing a paragraph share a block_id.
    first_three = page.words[:3]
    assert len({w.block_id for w in first_three}) == 1


def test_ocr_path_populates_block_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCR'd words inherit whatever block_id PyMuPDF assigns; the test
    asserts that the *field is populated* from the tuple, not that the
    value is any specific number."""
    from kuroi.core import pdf as pdf_module

    captured: dict[str, list[tuple[float, float, float, float, str, int, int, int]]] = {}

    class FakePage:
        def __init__(self) -> None:
            self.number = 1

        def get_text(self, mode: str, **kwargs: object) -> list[tuple[Any, ...]]:
            # Native pass returns words with block_id=0; OCR pass overrides with block_id=42.
            if "textpage" in kwargs:
                tup = (0.0, 0.0, 1.0, 1.0, "ocr", 42, 0, 0)
                captured["ocr_words"] = [tup]
                return [tup]
            return [(0.0, 0.0, 1.0, 1.0, "native", 0, 0, 0)]

        def get_images(self) -> list[object]:
            return [object()]  # trigger the OCR branch

        def get_textpage_ocr(self, **kwargs: object) -> object:
            return object()

    class FakeDoc:
        page_count = 1

        def __getitem__(self, _: int) -> FakePage:
            return FakePage()

        def close(self) -> None:
            pass

    monkeypatch.setattr(pdf_module.pymupdf, "open", lambda _: FakeDoc())
    monkeypatch.setattr(pdf_module.shutil, "which", lambda _: "/usr/bin/tesseract")

    result = pdf_module.extract_word_index(Path("ignored.pdf"))

    assert result.ocr_page_count == 1
    page = result.pages[0]
    assert len(page.words) == 1
    assert page.words[0].block_id == 42
    assert page.words[0].text == "ocr"


def test_serialize_for_llm_default_no_block_tags() -> None:
    from kuroi.core.pdf import Page, Word, serialize_for_llm

    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Hello", bbox=(0, 0, 1, 1), block_id=3),
                Word(idx=1, text="world", bbox=(1, 0, 2, 1), block_id=3),
                Word(idx=2, text="Other", bbox=(0, 1, 1, 2), block_id=4),
            ),
        ),
    )

    text = serialize_for_llm(pages)

    # Default path: no <block> markers anywhere.
    assert "<block" not in text
    assert text == '<page n="1">\n[0]Hello [1]world [2]Other\n</page>'


def _layout_pages() -> tuple[Page, ...]:
    from kuroi.core.pdf import Page, Word

    return (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Heading", bbox=(0, 0, 1, 1), block_id=7),
                Word(idx=1, text="First", bbox=(0, 1, 1, 2), block_id=8),
                Word(idx=2, text="paragraph", bbox=(1, 1, 2, 2), block_id=8),
                Word(idx=3, text="Footer", bbox=(0, 9, 1, 10), block_id=12),
            ),
        ),
    )


def test_serialize_for_llm_layout_aware_wraps_blocks() -> None:
    from kuroi.core.pdf import serialize_for_llm

    text = serialize_for_llm(_layout_pages(), layout_aware=True)

    # Three blocks emitted in document order; idx markers preserved inside.
    assert '<block id="7">[0]Heading</block>' in text
    assert '<block id="8">[1]First [2]paragraph</block>' in text
    assert '<block id="12">[3]Footer</block>' in text
    assert text.startswith('<page n="1">')
    assert text.endswith("</page>")


def test_serialize_for_llm_layout_aware_collapses_consecutive_same_block() -> None:
    from kuroi.core.pdf import Page, Word, serialize_for_llm

    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="a", bbox=(0, 0, 1, 1), block_id=5),
                Word(idx=1, text="b", bbox=(1, 0, 2, 1), block_id=5),
                Word(idx=2, text="c", bbox=(2, 0, 3, 1), block_id=5),
            ),
        ),
    )

    text = serialize_for_llm(pages, layout_aware=True)

    # One block tag wraps all three words; no spurious second tag.
    assert text.count('<block id="5">') == 1
    assert '<block id="5">[0]a [1]b [2]c</block>' in text


def test_serialize_for_llm_layout_aware_split_block_across_pages() -> None:
    """A block_id that appears on two pages emits twice — once per page —
    because PyMuPDF block numbering is per-page."""
    from kuroi.core.pdf import Page, Word, serialize_for_llm

    pages = (
        Page(
            number=1,
            words=(Word(idx=0, text="a", bbox=(0, 0, 1, 1), block_id=2),),
        ),
        Page(
            number=2,
            words=(Word(idx=0, text="b", bbox=(0, 0, 1, 1), block_id=2),),
        ),
    )

    text = serialize_for_llm(pages, layout_aware=True)

    assert text.count('<block id="2">') == 2
    assert '<page n="1">\n<block id="2">[0]a</block>\n</page>' in text
    assert '<page n="2">\n<block id="2">[0]b</block>\n</page>' in text


def test_extract_word_index_reports_total_pages(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["page one", "page two", "page three"])
    result = extract_word_index(pdf)
    assert result.total_pages == 3
    # No selection: total_pages must equal the returned page count.
    # Task 4 will introduce the case where these diverge.
    assert result.total_pages == len(result.pages)


def test_index_by_number_maps_pages_by_1indexed_number(
    make_pdf: Callable[..., Path],
) -> None:
    pdf = make_pdf(["one", "two", "three"])
    result = extract_word_index(pdf)
    lookup = index_by_number(result.pages)

    assert set(lookup.keys()) == {1, 2, 3}
    for num, page in lookup.items():
        assert page.number == num
