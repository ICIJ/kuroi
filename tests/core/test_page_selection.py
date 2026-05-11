"""Tests for --pages spec parsing and validation."""

from __future__ import annotations

import pytest

from kuroi.core.page_selection import PageSelectionError, parse


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
