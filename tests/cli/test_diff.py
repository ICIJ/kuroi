import json
from pathlib import Path

import pymupdf
from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def _make_pair(tmp_path: Path, original_text: str, redacted_text: str) -> tuple[Path, Path]:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    for text, p in ((original_text, a), (redacted_text, b)):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        doc.save(str(p))
        doc.close()
    return a, b


def test_diff_text_format_summarizes_each_page(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "Sarah Chen was here", "         was here")
    result = runner.invoke(app, ["diff", str(orig), str(red)])
    assert result.exit_code == 0
    assert "Page 1" in result.stdout
    assert "redaction" in result.stdout.lower()


def test_diff_json_format_emits_ndjson(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "Sarah Chen was here", "         was here")
    result = runner.invoke(app, ["diff", str(orig), str(red), "--format", "json"])
    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["page"] == 1
    assert "before_text" in parsed[0]
    assert "after_text" in parsed[0]
    assert "redactions" in parsed[0]
    assert isinstance(parsed[0]["redactions"], list)


def test_diff_html_format_writes_self_contained_file(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "Sarah Chen was here", "         was here")
    out = tmp_path / "diff.html"
    result = runner.invoke(
        app,
        [
            "diff",
            str(orig),
            str(red),
            "--format",
            "html",
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0
    body = out.read_text()
    assert "<html" in body
    assert "<style" in body  # inline CSS, no external stylesheet
    assert "Sarah" in body
    assert "Page 1" in body


def test_diff_html_format_refuses_tty(tmp_path: Path):
    orig, red = _make_pair(tmp_path, "x", "y")
    result = runner.invoke(app, ["diff", str(orig), str(red), "--format", "html"])
    assert result.exit_code == 2
    assert "requires -o" in result.stdout
