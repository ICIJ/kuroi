"""Shared pytest fixtures."""

from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest


@pytest.fixture
def make_pdf(tmp_path: Path) -> Callable[..., Path]:
    """Build a PDF from a list of page text strings.

    Each page string is rendered with default font at (72, 72).
    Returns the path to the saved PDF.
    """

    def _make(pages: list[str], filename: str = "test.pdf") -> Path:
        doc = pymupdf.open()
        for text in pages:
            page = doc.new_page()
            page.insert_text((72, 72), text, fontsize=11)
        out = tmp_path / filename
        doc.save(str(out))
        doc.close()
        return out

    return _make


@pytest.fixture
def make_overlay_pdf(tmp_path: Path) -> Callable[..., Path]:
    """Build a PDF with a black rectangle drawn OVER text — overlay-only redaction.

    The text remains in the content stream and is still extractable, which is
    exactly the failure mode kuroi verify must detect.
    """

    def _make(text: str, redact_rect: tuple[float, float, float, float]) -> Path:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        page.draw_rect(pymupdf.Rect(*redact_rect), color=(0, 0, 0), fill=(0, 0, 0))
        out = tmp_path / "overlay.pdf"
        doc.save(str(out))
        doc.close()
        return out

    return _make
