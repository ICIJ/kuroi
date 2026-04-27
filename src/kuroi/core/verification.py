"""Deterministic leak detection on already-redacted PDFs.

The verifier never calls an LLM. It looks for the well-known failure modes
where a document appears redacted on screen but the underlying text or
metadata is still recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

LeakKind = Literal["text_under_overlay", "metadata", "unapplied_redact_annot"]


@dataclass(frozen=True)
class Leak:
    page: int  # 1-indexed; 0 for document-level (e.g. metadata)
    kind: LeakKind
    bbox: tuple[float, float, float, float] | None
    recovered_text: str
    detail: str


def scan_text_under_overlays(pdf_path: Path) -> list[Leak]:
    """Find drawn rectangles or annotations that have selectable text beneath.

    Strategy: enumerate every filled rectangle on each page (drawing operators
    in the content stream) and check whether the page's text-layer extraction
    returns any words whose bbox is contained in that rectangle.
    """
    leaks: list[Leak] = []
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            words = page.get_text("words")  # type: ignore[no-untyped-call]
            rects = _filled_rectangles(page)
            for rect in rects:
                covered: list[str] = []
                for w in words:
                    wx0, wy0, wx1, wy1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
                    if _box_contains(rect, (wx0, wy0, wx1, wy1)):
                        covered.append(str(w[4]))
                if covered:
                    leaks.append(
                        Leak(
                            page=page_idx + 1,
                            kind="text_under_overlay",
                            bbox=rect,
                            recovered_text=" ".join(covered),
                            detail="drawn rectangle covers selectable text",
                        )
                    )
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return leaks


def _filled_rectangles(
    page: pymupdf.Page,
) -> list[tuple[float, float, float, float]]:
    """Return rectangles drawn with a fill on this page."""
    out: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        if drawing.get("fill") is None:
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        out.append((float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)))
    return out


def _box_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    tol: float = 1.0,
) -> bool:
    """True if `inner` fits inside `outer` within `tol` PDF points."""
    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner
    return ix0 >= ox0 - tol and iy0 >= oy0 - tol and ix1 <= ox1 + tol and iy1 <= oy1 + tol
