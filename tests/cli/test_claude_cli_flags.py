"""Tests for `kuroi run` CLI flags that resolve into ClaudeCliProvider."""

from __future__ import annotations

import typer

from kuroi.cli import app


def test_claude_cli_path_flag_is_recognized() -> None:
    cmd = typer.main.get_command(app).get_command(None, "run")  # type: ignore[attr-defined]
    flags = {opt for param in cmd.params for opt in param.opts}
    assert "--claude-cli-path" in flags
    assert "--claude-cli-timeout" in flags
