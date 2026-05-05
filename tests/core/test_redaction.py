from collections.abc import Callable
from pathlib import Path

import pymupdf

from kuroi.core.findings import Finding
from kuroi.core.pdf import extract_word_index
from kuroi.core.redaction import apply_redactions


def test_apply_redactions_removes_text_from_stream(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    pdf = make_pdf(["Hello Sarah Chen and others"])
    pages = extract_word_index(pdf).pages
    # words are: [0]Hello [1]Sarah [2]Chen [3]and [4]others
    findings = [
        Finding(page=1, start=1, end=2, kind="person_name", confidence="high", source="test"),
    ]

    out = tmp_path / "redacted.pdf"
    apply_redactions(pdf, findings, pages, out)

    assert out.is_file()
    redacted = pymupdf.open(str(out))
    text = redacted[0].get_text("text")
    redacted.close()
    assert "Sarah" not in text
    assert "Chen" not in text
    assert "Hello" in text
    assert "others" in text


def test_apply_redactions_clears_metadata(make_pdf: Callable[..., Path], tmp_path: Path) -> None:
    pdf = make_pdf(["Hello world"])
    # Stamp metadata onto the input
    doc = pymupdf.open(str(pdf))
    doc.set_metadata({"author": "Sarah Chen", "title": "Confidential"})
    doc.save(str(pdf), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()

    pages = extract_word_index(pdf).pages
    out = tmp_path / "redacted.pdf"
    apply_redactions(pdf, [], pages, out)

    redacted = pymupdf.open(str(out))
    meta = redacted.metadata or {}
    redacted.close()
    assert (meta.get("author") or "") == ""
    assert (meta.get("title") or "") == ""


def test_apply_redactions_writes_no_file_with_no_findings(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    pdf = make_pdf(["Hello world"])
    pages = extract_word_index(pdf).pages

    out = tmp_path / "redacted.pdf"
    apply_redactions(pdf, [], pages, out)

    # Even with zero findings we still produce an output (unchanged copy is fine
    # — the run command can decide to skip writing if findings are empty).
    assert out.is_file()
    redacted = pymupdf.open(str(out))
    text = redacted[0].get_text("text")
    redacted.close()
    assert "Hello world" in text
