from pathlib import Path

import pymupdf
import pytest

from kuroi.core.diff import Diff, compute_diff


def _two_pdfs(tmp_path: Path, original_text: str, redacted_text: str) -> tuple[Path, Path]:
    orig_path = tmp_path / "orig.pdf"
    red_path = tmp_path / "red.pdf"
    for text, p in ((original_text, orig_path), (redacted_text, red_path)):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        doc.save(str(p))
        doc.close()
    return orig_path, red_path


def test_compute_diff_finds_removed_words(tmp_path: Path):
    orig, red = _two_pdfs(
        tmp_path,
        "Sarah Chen was here",
        " was here",  # "Sarah Chen" removed
    )
    diff = compute_diff(orig, red)

    assert isinstance(diff, Diff)
    assert len(diff.pages) == 1
    page = diff.pages[0]
    assert page.page_number == 1
    redacted_texts = [r.before_text for r in page.redactions]
    assert "Sarah" in redacted_texts
    assert "Chen" in redacted_texts


def test_compute_diff_with_no_changes_yields_no_redactions(tmp_path: Path):
    orig, red = _two_pdfs(tmp_path, "same content", "same content")
    diff = compute_diff(orig, red)
    assert sum(len(p.redactions) for p in diff.pages) == 0


def test_compute_diff_mismatched_page_counts_raises(tmp_path: Path):
    orig_path = tmp_path / "orig.pdf"
    red_path = tmp_path / "red.pdf"

    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(str(orig_path))
    doc.close()

    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(red_path))
    doc.close()

    with pytest.raises(ValueError, match="page count"):
        compute_diff(orig_path, red_path)
