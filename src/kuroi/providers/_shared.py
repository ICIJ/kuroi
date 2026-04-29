"""Helpers shared by every concrete Provider implementation."""

from __future__ import annotations

from typing import Any

from kuroi.core.findings import Confidence, Finding
from kuroi.core.pdf import Page


def parse_findings_payload(
    payload: dict[str, Any],
    pages: tuple[Page, ...],
    *,
    source: str,
) -> list[Finding]:
    """Convert a model's JSON response into Finding objects.

    Findings whose (page, start, end) reference does not exist in `pages` are
    silently dropped — a model that hallucinates indices cannot redact words
    that don't exist.
    """
    page_lookup = {p.number: p for p in pages}
    valid_confidences: tuple[Confidence, ...] = ("high", "medium", "low")
    out: list[Finding] = []
    for item in payload.get("findings", []):
        try:
            pg = int(item["page"])
            start = int(item["start"])
            end = int(item["end"])
            kind = str(item["kind"])
            conf_raw: Any = item.get("confidence", "medium")
        except (KeyError, TypeError, ValueError):
            continue
        conf = "medium" if conf_raw not in valid_confidences else conf_raw
        page = page_lookup.get(pg)
        if page is None:
            continue
        if not (0 <= start <= end < len(page.words)):
            continue
        out.append(
            Finding(
                page=pg,
                start=start,
                end=end,
                kind=kind,
                confidence=conf,  # type: ignore[arg-type]
                source=source,
            )
        )
    return out
