"""Tests for `kuroi run` CLI flags that resolve into ClaudeCliProvider."""

from __future__ import annotations

from typer.testing import CliRunner

from kuroi.cli import app


def test_claude_cli_path_flag_is_recognized() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"], env={"COLUMNS": "200"})
    assert "--claude-cli-path" in result.stdout
    assert "--claude-cli-timeout" in result.stdout
