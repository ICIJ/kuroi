"""Tests for the undo interactive picker wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kuroi.cli.undo_picker import (
    PageSentinel,
    _build_choices,
    _expand_page_sentinels,
    pick_findings,
)
from kuroi.core.audit_replay import ReplayableFinding, ReplayableSession


def _session() -> ReplayableSession:
    return ReplayableSession(
        session_id="x",
        input_path=Path("/i"),
        output_path=Path("/o"),
        findings=(
            ReplayableFinding(3, 12, 14, "email", "high", "llm", None),  # 0
            ReplayableFinding(3, 20, 21, "person", "medium", "rules", None),  # 1
            ReplayableFinding(5, 0, 2, "person", "high", "llm", None),  # 2
            ReplayableFinding(7, 4, 6, "email", "high", "llm", None),  # 3
        ),
    )


def test_build_choices_groups_by_page_with_headers() -> None:
    session = _session()
    text_by_index = {0: "alice@example.com", 1: "Alice", 2: "Bob", 3: "carol@example.com"}
    choices = _build_choices(session, text_by_index=text_by_index, pages_filter=None)

    # 3 pages → 3 page-header rows + 4 finding rows
    assert len(choices) == 7
    titles = [c.title for c in choices]
    assert "Page 3" in titles[0]
    assert "Page 5" in next(t for t in titles if "Page 5" in t)


def test_build_choices_pages_filter_drops_other_pages() -> None:
    session = _session()
    text_by_index = {0: "a", 1: "b", 2: "c", 3: "d"}
    choices = _build_choices(session, text_by_index=text_by_index, pages_filter=(3,))
    # only page 3 group: 1 header + 2 findings
    assert len(choices) == 3


def test_expand_page_sentinels_expands_to_all_indices_on_page() -> None:
    session = _session()
    selected: list[Any] = [PageSentinel(3), 2]
    result = _expand_page_sentinels(selected, session)
    assert result == (0, 1, 2)  # page 3 → indices 0,1; explicit 2


def test_expand_page_sentinels_dedupes() -> None:
    session = _session()
    selected: list[Any] = [PageSentinel(3), 0, 1]
    result = _expand_page_sentinels(selected, session)
    assert result == (0, 1)


def test_pick_findings_returns_empty_on_no_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    text_by_index = {i: "x" for i in range(4)}

    class FakeAsk:
        def ask(self) -> list:
            return []

    monkeypatch.setattr(
        "kuroi.cli.undo_picker.questionary.checkbox",
        lambda *args, **kwargs: FakeAsk(),
    )

    result = pick_findings(session, text_by_index=text_by_index, pages_filter=None)
    assert result == ()


def test_pick_findings_keyboardinterrupt_on_none(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    text_by_index = {i: "x" for i in range(4)}

    class FakeAsk:
        def ask(self) -> None:
            return None  # questionary returns None on Ctrl-C

    monkeypatch.setattr(
        "kuroi.cli.undo_picker.questionary.checkbox",
        lambda *args, **kwargs: FakeAsk(),
    )

    with pytest.raises(KeyboardInterrupt):
        pick_findings(session, text_by_index=text_by_index, pages_filter=None)


def test_pick_findings_returns_selected_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    text_by_index = {i: "x" for i in range(4)}

    class FakeAsk:
        def ask(self) -> list:
            return [0, 3]  # pick first and last findings

    monkeypatch.setattr(
        "kuroi.cli.undo_picker.questionary.checkbox",
        lambda *args, **kwargs: FakeAsk(),
    )

    result = pick_findings(session, text_by_index=text_by_index, pages_filter=None)
    assert result == (0, 3)
