"""kuroi undo — restore the most recent backup."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.backup import latest_backup

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
