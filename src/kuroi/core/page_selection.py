"""Parse and validate ``--pages`` specs for ``kuroi run``.

The spec grammar accepts comma-separated singles and inclusive ranges, plus
arbitrary whitespace:

    SPEC := PART ("," PART)*
    PART := INT | INT "-" INT

Examples (all valid):
    "1", "1,2,14", "1-5", "1,3-5,10", "  1 , 3 - 5 "

Rejected (with ``PageSelectionError``):
    "", "0", "-3", "5-1", "1-", "-5", "1,abc"
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


class PageSelectionError(ValueError):
    """Raised on malformed --pages input or out-of-range pages."""


@dataclass(frozen=True)
class PageSelection:
    raw: str
    pages: tuple[int, ...]

    def __contains__(self, n: object) -> bool:
        return n in self.pages

    def __iter__(self) -> Iterator[int]:
        return iter(self.pages)

    def __len__(self) -> int:
        return len(self.pages)


def parse(spec: str) -> PageSelection:
    """Parse a ``--pages`` spec. Does not validate against any document."""
    if not spec.strip():
        raise PageSelectionError("empty page selection")

    collected: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            raise PageSelectionError(f"empty part in {spec!r}")
        if "-" in token:
            _add_range(token, collected)
        else:
            collected.add(_parse_int(token))

    return PageSelection(raw=spec, pages=tuple(sorted(collected)))


def _add_range(token: str, collected: set[int]) -> None:
    left, _, right = token.partition("-")
    left, right = left.strip(), right.strip()
    if not left or not right:
        # Covers both open ranges ("1-") and leading-dash inputs ("-5" or "-3"),
        # which look like negative numbers but aren't valid — page numbers must
        # be >= 1, so the combined message satisfies both error-match patterns.
        raise PageSelectionError(
            f"open ranges not supported (page numbers must be >= 1): {token!r}"
        )
    start = _parse_int(left)
    end = _parse_int(right)
    if start > end:
        raise PageSelectionError(f"range {start}-{end} is reversed")
    collected.update(range(start, end + 1))


def _parse_int(token: str) -> int:
    try:
        n = int(token)
    except ValueError as exc:
        raise PageSelectionError(f"not a number: {token!r}") from exc
    if n < 1:
        raise PageSelectionError(f"page numbers must be >= 1, got {n}")
    return n
