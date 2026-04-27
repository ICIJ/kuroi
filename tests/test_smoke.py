"""Smoke tests — the CLI wires up at all."""

from typer.testing import CliRunner

from kuroi import __version__
from kuroi.cli import app


def test_version_flag_prints_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, [])
    # no_args_is_help=True → Typer prints help and exits 2 (Click's "no command")
    assert result.exit_code == 2
    assert "Usage:" in result.stdout
