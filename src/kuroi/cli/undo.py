"""kuroi undo — restore the most recent backup."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.backup import latest_backup, sweep_backups
from kuroi.core.config import ConfigError, ConfigOverrides, resolve_config, xdg_config_home

undo_app = typer.Typer(invoke_without_command=True)
console = Console()


@undo_app.callback(invoke_without_command=True)
def undo(
    yes: bool = typer.Option(False, "-y", help="Skip the restore confirmation."),
    backup_dir: Path = typer.Option(
        Path.home() / "Documents" / "kuroi-backups",
        "--backup-dir",
    ),
) -> None:
    """Restore the original file from the most recent backup."""
    try:
        config = resolve_config(
            ConfigOverrides(),
            env=os.environ,
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)

    bak = latest_backup(backup_dir)
    if bak is None:
        console.print(f"  No backup found in {backup_dir}.")
        raise typer.Exit(code=1)

    console.print(f"  Last backup: {bak.timestamp}")
    console.print(f"  Will restore: {bak.original_path}")

    if not yes:
        confirm = typer.confirm("Restore now?", default=True)
        if not confirm:
            raise typer.Exit(code=0)

    bak.original_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak.copy_path, bak.original_path)
    console.print("  Restored.")
