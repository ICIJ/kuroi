"""Apply Findings to a PDF using PyMuPDF's true-redaction operation.

True redaction (page.apply_redactions) rewrites the content stream so the
removed text is no longer recoverable, unlike a black box drawn over the page.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from kuroi.core.findings import Finding, bbox_union
from kuroi.core.pdf import Page


def apply_redactions(
    pdf_path: Path,
    findings: list[Finding],
    pages: tuple[Page, ...],
    output_path: Path,
) -> None:
    """Write a redacted copy of `pdf_path` to `output_path`.

    Findings are translated into bbox unions over their word ranges, registered
    as redact annotations, and then applied page-by-page.
    """
    page_lookup = {p.number: p for p in pages}
    findings_by_page: dict[int, list[Finding]] = {}
    for f in findings:
        findings_by_page.setdefault(f.page, []).append(f)

    doc = pymupdf.open(str(pdf_path))  # type: ignore[no-untyped-call]
    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_num = page_idx + 1
            page_findings = findings_by_page.get(page_num, [])
            page_words = page_lookup.get(page_num)
            if page_words is None or not page_findings:
                continue
            for f in page_findings:
                rect = _finding_bbox(f, page_words)
                if rect is None:
                    continue
                page.add_redact_annot(pymupdf.Rect(*rect), fill=(0, 0, 0))  # type: ignore[no-untyped-call]
            # Apply removes the underlying text, not just draw black boxes.
            page.apply_redactions()
        # Clear standard PDF metadata fields and the XMP stream so the verifier
        # (which flags any non-empty metadata) doesn't trip on input-document
        # residue. Without this, every real PDF would fail the verification gate.
        doc.set_metadata({})
        xmp = doc.xref_xml_metadata()  # type: ignore[no-untyped-call]
        if xmp:
            doc.del_xml_metadata()  # type: ignore[no-untyped-call]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)  # type: ignore[no-untyped-call]
    finally:
        doc.close()  # type: ignore[no-untyped-call]


_DESCENDER_PADDING = 1.5  # PDF points; gives the redact box room for descenders


def _finding_bbox(finding: Finding, page: Page) -> tuple[float, float, float, float] | None:
    """Compute the rectangle to redact for a single finding."""
    if finding.start < 0 or finding.end >= len(page.words):
        return None
    boxes = [page.words[i].bbox for i in range(finding.start, finding.end + 1)]
    x0, y0, x1, y1 = bbox_union(boxes)
    return (x0, y0 - _DESCENDER_PADDING, x1, y1 + _DESCENDER_PADDING)
