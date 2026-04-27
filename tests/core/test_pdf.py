from collections.abc import Callable
from pathlib import Path

from kuroi.core.pdf import extract_word_index, serialize_for_llm


def test_extract_word_index_returns_words_with_bboxes(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Hello world"])

    pages = extract_word_index(pdf)

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
    pages = extract_word_index(pdf)

    text = serialize_for_llm(pages)

    assert '<page n="1">' in text
    assert '<page n="2">' in text
    assert "[0]Hello" in text
    assert "[1]world" in text
    assert "[0]Second" in text
    assert "[1]page" in text
