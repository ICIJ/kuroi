from collections.abc import Callable
from pathlib import Path

import pymupdf

from kuroi.core.verification import (
    _filled_rectangles,
    scan_metadata,
    scan_text_under_overlays,
    verify_pdf,
)


def test_scan_text_under_overlays_finds_leak(
    make_overlay_pdf: Callable[..., Path],
) -> None:
    # "Hello world" rendered at (72, 72), 11pt → text bbox is ~(72, 60, 127, 75)
    pdf = make_overlay_pdf("Hello world", redact_rect=(70.0, 58.0, 130.0, 77.0))

    leaks = scan_text_under_overlays(pdf)

    assert len(leaks) >= 1
    leak = leaks[0]
    assert leak.page == 1
    assert "Hello" in leak.recovered_text or "world" in leak.recovered_text


def test_scan_text_under_overlays_clean(make_pdf: Callable[..., Path]) -> None:
    # No drawn rectangles, no annotations → no overlay leaks
    pdf = make_pdf(["Hello world"])

    leaks = scan_text_under_overlays(pdf)

    assert leaks == []


def test_scan_text_under_overlays_ignores_white_page_background(tmp_path: Path) -> None:
    """A white-fill page background is not a redaction overlay.

    Some PDF generators emit a full-page white-fill rectangle behind the text;
    that should not be flagged as a text-under-overlay leak.
    """
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()  # type: ignore[no-untyped-call]
    page.draw_rect(  # type: ignore[no-untyped-call]
        pymupdf.Rect(0, 0, 612, 792), color=(1, 1, 1), fill=(1, 1, 1)
    )
    page.insert_text((72, 72), "Hello world", fontsize=11)  # type: ignore[no-untyped-call]
    out = tmp_path / "white_bg.pdf"
    doc.save(str(out))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]

    leaks = scan_text_under_overlays(out)

    assert leaks == []


def test_filled_rectangles_excludes_thin_decorative_rects(tmp_path: Path) -> None:
    # PACER-style PDFs scatter sub-5pt dark rects (table separators, list
    # bullets) across every page. They are not redaction overlays and must not
    # be candidates: a body-text glyph's cap-height alone is ~6pt, so anything
    # shorter physically cannot obscure characters.
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(72, 72, 200, 92), color=(0, 0, 0), fill=(0, 0, 0))
    page.draw_rect(pymupdf.Rect(72, 200, 200, 203), color=(0, 0, 0), fill=(0, 0, 0))
    out = tmp_path / "mixed.pdf"
    doc.save(str(out))
    doc.close()

    doc = pymupdf.open(str(out))
    rects = _filled_rectangles(doc[0])
    doc.close()

    assert len(rects) == 1
    assert rects[0] == (72.0, 72.0, 200.0, 92.0)


def test_scan_text_under_overlays_ignores_pure_punctuation_leak(tmp_path: Path) -> None:
    # A redaction overlay that only happens to enclose a stray period (or any
    # non-alphanumeric glyph) is not a meaningful information leak. This is the
    # PACER false positive that caused real-world verify failures.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), ".", fontsize=11)
    page.draw_rect(pymupdf.Rect(70, 60, 90, 78), color=(0, 0, 0), fill=(0, 0, 0))
    out = tmp_path / "punct_only.pdf"
    doc.save(str(out))
    doc.close()

    leaks = scan_text_under_overlays(out)

    assert leaks == []


def test_scan_metadata_flags_author(tmp_path: Path) -> None:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()  # type: ignore[no-untyped-call]
    page.insert_text((72, 72), "innocuous body", fontsize=11)  # type: ignore[no-untyped-call]
    doc.set_metadata({"author": "Sarah Chen", "title": "", "creator": "", "producer": ""})  # type: ignore[no-untyped-call]
    out = tmp_path / "with_author.pdf"
    doc.save(str(out))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]

    leaks = scan_metadata(out)

    assert any(leak.kind == "metadata" and "Sarah Chen" in leak.recovered_text for leak in leaks)


def test_scan_metadata_clean(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["body text"])
    leaks = scan_metadata(pdf)
    # Default-empty metadata fields produce no leaks
    assert leaks == []


def test_scan_metadata_flags_xmp_stream(tmp_path: Path) -> None:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    page = doc.new_page()  # type: ignore[no-untyped-call]
    page.insert_text((72, 72), "innocuous body", fontsize=11)  # type: ignore[no-untyped-call]
    xmp_payload = (
        '<?xpacket begin=""?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
        "<dc:creator>Sarah Chen</dc:creator>"
        '</x:xmpmeta><?xpacket end="w"?>'
    )
    doc.set_xml_metadata(xmp_payload)  # type: ignore[no-untyped-call]
    out = tmp_path / "with_xmp.pdf"
    doc.save(str(out))  # type: ignore[no-untyped-call]
    doc.close()  # type: ignore[no-untyped-call]

    leaks = scan_metadata(out)

    assert any(
        leak.kind == "metadata" and "Sarah Chen" in leak.recovered_text for leak in leaks
    )


def test_verify_pdf_clean(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["body text"])
    report = verify_pdf(pdf)
    assert report.passed is True
    assert report.leaks == ()


def test_verify_pdf_fails_on_overlay(make_overlay_pdf: Callable[..., Path]) -> None:
    pdf = make_overlay_pdf("Hello world", redact_rect=(70.0, 58.0, 130.0, 77.0))
    report = verify_pdf(pdf)
    assert report.passed is False
    assert any(leak.kind == "text_under_overlay" for leak in report.leaks)
