from collections.abc import Callable
from pathlib import Path

from kuroi.core.verification import scan_text_under_overlays


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
