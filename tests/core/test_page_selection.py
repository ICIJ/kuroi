"""Tests for --pages spec parsing and validation."""

from __future__ import annotations

import pytest

from kuroi.core.page_selection import PageSelectionError, parse, validate


def test_parse_single() -> None:
    result = parse("5")
    assert result.pages == (5,)
    assert result.raw == "5"


def test_parse_list() -> None:
    result = parse("1,2,14")
    assert result.pages == (1, 2, 14)
    assert result.raw == "1,2,14"


def test_parse_range() -> None:
    result = parse("1-5")
    assert result.pages == (1, 2, 3, 4, 5)


def test_parse_mixed() -> None:
    result = parse("1,3-5,10")
    assert result.pages == (1, 3, 4, 5, 10)


def test_parse_whitespace_tolerant() -> None:
    result = parse("  1 , 3 - 5 ")
    assert result.pages == (1, 3, 4, 5)
    # raw preserves the user's original string verbatim
    assert result.raw == "  1 , 3 - 5 "


def test_parse_sorts_and_dedupes() -> None:
    result = parse("3,1,2,3")
    assert result.pages == (1, 2, 3)


def test_parse_rejects_empty() -> None:
    with pytest.raises(PageSelectionError, match="empty"):
        parse("")


def test_parse_rejects_whitespace_only() -> None:
    with pytest.raises(PageSelectionError, match="empty"):
        parse("   ")


def test_parse_rejects_zero() -> None:
    with pytest.raises(PageSelectionError, match=">= 1"):
        parse("0")


def test_parse_rejects_negative() -> None:
    with pytest.raises(PageSelectionError, match=">= 1"):
        parse("-3")


def test_parse_rejects_reversed_range() -> None:
    with pytest.raises(PageSelectionError, match="reversed"):
        parse("5-1")


def test_parse_rejects_open_range_trailing() -> None:
    with pytest.raises(PageSelectionError, match="open ranges"):
        parse("1-")


def test_parse_rejects_open_range_leading() -> None:
    with pytest.raises(PageSelectionError, match="open ranges"):
        parse("-5")


def test_parse_rejects_non_numeric() -> None:
    with pytest.raises(PageSelectionError, match="not a number"):
        parse("1,abc")


def test_page_selection_contains() -> None:
    selection = parse("1,3-5,10")
    assert 1 in selection
    assert 4 in selection
    assert 10 in selection
    assert 2 not in selection
    assert 11 not in selection


def test_page_selection_len_and_iter() -> None:
    selection = parse("1,3-5,10")
    assert len(selection) == 5
    assert list(selection) == [1, 3, 4, 5, 10]


def test_validate_passes_when_in_range() -> None:
    selection = parse("1,5,10")
    # Returns the same selection unchanged.
    result = validate(selection, page_count=10)
    assert result is selection


def test_validate_rejects_out_of_range_single() -> None:
    selection = parse("99")
    with pytest.raises(PageSelectionError, match="99 not in document"):
        validate(selection, page_count=10)


def test_validate_rejects_out_of_range_partial() -> None:
    # No silent trim — even if 1 and 2 are valid, the presence of 99
    # is a hard error.
    selection = parse("1,2,99")
    with pytest.raises(PageSelectionError, match="99 not in document"):
        validate(selection, page_count=10)


def test_validate_rejects_out_of_range_partial_range() -> None:
    selection = parse("1-15")
    with pytest.raises(PageSelectionError) as exc_info:
        validate(selection, page_count=10)
    # Message names the out-of-range pages and the document size
    msg = str(exc_info.value)
    assert "11" in msg
    assert "15" in msg
    assert "1-10" in msg


def test_validate_message_includes_document_size() -> None:
    selection = parse("99")
    with pytest.raises(PageSelectionError, match=r"1-10"):
        validate(selection, page_count=10)
