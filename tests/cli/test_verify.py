from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from kuroi.cli import app


def test_verify_clean_pdf_exits_0(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["body text"])
    runner = CliRunner()
    result = runner.invoke(app, ["verify", str(pdf)])
    assert result.exit_code == 0
    assert "PASS" in result.stdout or "no residual" in result.stdout


def test_verify_overlay_pdf_exits_4(make_overlay_pdf: Callable[..., Path]) -> None:
    pdf = make_overlay_pdf("Hello world", redact_rect=(70.0, 58.0, 130.0, 77.0))
    runner = CliRunner()
    result = runner.invoke(app, ["verify", str(pdf)])
    assert result.exit_code == 4
    assert "FAIL" in result.stdout
