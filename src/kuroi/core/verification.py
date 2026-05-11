"""Deterministic leak detection on already-redacted PDFs.

The verifier never calls an LLM. It looks for the well-known failure modes
where a document appears redacted on screen but the underlying text or
metadata is still recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pymupdf

LeakKind = Literal["text_under_overlay", "metadata"]


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
                if covered and _has_substantive_content(covered):
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


def _has_substantive_content(covered: list[str]) -> bool:
    """True if any character in `covered` is alphanumeric.

    Filters the PACER-style false positive where a decorative dark rect
    coincidentally encloses the bbox of a stray punctuation glyph (period,
    comma, hyphen). A rect that contains only punctuation and whitespace
    cannot leak meaningful information regardless of whether kuroi or the
    source document drew it.
    """
    return any(ch.isalnum() for ch in "".join(covered))


_DARK_FILL_THRESHOLD = 0.5  # all RGB components below this count as redaction-dark

# Below this height, a filled rect cannot physically obscure body-text glyphs
# (cap-height ~6pt at 8pt body). Anything thinner is decoration: PACER-style
# table separators, list bullets, hairline rules. kuroi's own apply_redactions
# always produces rects >= word-bbox-height + 3pt descender padding, so its
# real overlays clear this filter by a wide margin.
_MIN_OVERLAY_HEIGHT_PT = 5.0


def _is_redaction_fill(fill: object) -> bool:
    """True when `fill` is near-black across all RGB components.

    PDFs commonly include non-redaction filled rectangles (white page
    backgrounds, colored highlights). Only treat dark fills as candidate
    redaction overlays so the verifier doesn't trip on those.
    """
    if fill is None:
        return False
    try:
        rgb: tuple[Any, ...] = tuple(fill)[:3]  # type: ignore[arg-type]
    except TypeError:
        return False
    if not rgb:
        return False
    return all(float(c) < _DARK_FILL_THRESHOLD for c in rgb)


def _filled_rectangles(
    page: pymupdf.Page,
) -> list[tuple[float, float, float, float]]:
    """Return dark-filled rectangles drawn on this page.

    Light fills (e.g. a white page background) are excluded — they aren't
    redaction overlays, and treating them as such yields false-positive leaks.
    """
    out: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        if not _is_redaction_fill(drawing.get("fill")):
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        if float(rect.y1) - float(rect.y0) < _MIN_OVERLAY_HEIGHT_PT:
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


_METADATA_FIELDS = ("author", "title", "subject", "keywords", "creator", "producer")


def scan_metadata(pdf_path: Path) -> list[Leak]:
    """Flag any non-empty value in the PDF's standard metadata fields.

    A value being present is not automatically a leak in the user's eyes, but
    in the kuroi workflow the redacted output should have its metadata
    sanitized — anything left here should surface so the user can decide.
    """
    leaks: list[Leak] = []
    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    try:
        meta = doc.metadata or {}
        for field in _METADATA_FIELDS:
            value = (meta.get(field) or "").strip()
            if value:
                leaks.append(
                    Leak(
                        page=0,
                        kind="metadata",
                        bbox=None,
                        recovered_text=value,
                        detail=f"/{field.title()} field contains '{value}'",
                    )
                )
        # XMP metadata is a separate stream; non-empty XMP is worth surfacing.
        xmp = doc.xref_xml_metadata()  # type: ignore[no-untyped-call]
        if xmp:
            leaks.append(
                Leak(
                    page=0,
                    kind="metadata",
                    bbox=None,
                    recovered_text=xmp[:200],
                    detail="XMP metadata stream is non-empty",
                )
            )
    finally:
        doc.close()  # type: ignore[no-untyped-call]
    return leaks


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    leaks: tuple[Leak, ...]

    @property
    def summary(self) -> str:
        if self.passed:
            return "no residual leaks"
        kinds: dict[str, int] = {}
        for leak in self.leaks:
            kinds[leak.kind] = kinds.get(leak.kind, 0) + 1
        parts = [f"{count} {kind}" for kind, count in kinds.items()]
        return ", ".join(parts)


def verify_pdf(pdf_path: Path) -> VerificationReport:
    """Run every deterministic leak scanner and aggregate results."""
    leaks: list[Leak] = []
    leaks.extend(scan_text_under_overlays(pdf_path))
    leaks.extend(scan_metadata(pdf_path))
    return VerificationReport(passed=not leaks, leaks=tuple(leaks))
