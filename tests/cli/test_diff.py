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
