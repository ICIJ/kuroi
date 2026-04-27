from collections.abc import Callable
from pathlib import Path

import pymupdf

from kuroi.core.verification import scan_metadata, scan_text_under_overlays


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
