"""Interactive picker for `kuroi undo`.

Wraps `questionary.checkbox` with page-header group rows. Page headers are
encoded as `PageSentinel(page_number)` choice values; selecting a header
expands to every finding index on that page when results are unpacked.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any

import questionary

from kuroi.core.audit_replay import ReplayableSession


@dataclass(frozen=True)
class PageSentinel:
    page: int


def _truncate(text: str, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    if max_width <= 1:
        return "…"
    return text[: max_width - 1] + "…"


def _build_choices(
    session: ReplayableSession,
    *,
    text_by_index: dict[int, str],
    pages_filter: tuple[int, ...] | None,
) -> list[questionary.Choice]:
    indices_by_page: dict[int, list[int]] = {}
    for idx, f in enumerate(session.findings):
        if pages_filter is not None and f.page not in pages_filter:
            continue
        indices_by_page.setdefault(f.page, []).append(idx)

    term_width = shutil.get_terminal_size((80, 24)).columns
    text_width = max(20, term_width - 40)

    choices: list[questionary.Choice] = []
    for page in sorted(indices_by_page):
        count = len(indices_by_page[page])
        choices.append(
            questionary.Choice(
                title=f"Page {page}   ({count} finding{'s' if count != 1 else ''})",
                value=PageSentinel(page),
            )
        )
        for idx in indices_by_page[page]:
            f = session.findings[idx]
            text = _truncate(text_by_index.get(idx, ""), text_width)
            choices.append(
                questionary.Choice(
                    title=f"    {f.kind:<10}  {f.source:<10}  {text!r}",
                    value=idx,
                )
            )
    return choices


def _expand_page_sentinels(
    selected: list[Any],
    session: ReplayableSession,
) -> tuple[int, ...]:
    expanded: set[int] = set()
    for s in selected:
        if isinstance(s, PageSentinel):
            for idx, f in enumerate(session.findings):
                if f.page == s.page:
                    expanded.add(idx)
        elif isinstance(s, int):
            expanded.add(s)
    return tuple(sorted(expanded))


def pick_findings(
    session: ReplayableSession,
    *,
    text_by_index: dict[int, str],
    pages_filter: tuple[int, ...] | None,
) -> tuple[int, ...]:
    """Open the picker and return finding indices the user selected.

    Returns an empty tuple on confirmed empty selection. Raises
    `KeyboardInterrupt` if the user cancels (Ctrl-C).
    """
    choices = _build_choices(session, text_by_index=text_by_index, pages_filter=pages_filter)
    header = (
        f"Session {session.session_id} — {session.input_path.name}\n"
        "Use ↑/↓ to move, Space to toggle, Enter to confirm, Ctrl-C to cancel."
    )
    answer = questionary.checkbox(header, choices=choices).ask()
    if answer is None:
        raise KeyboardInterrupt
    return _expand_page_sentinels(answer, session)
