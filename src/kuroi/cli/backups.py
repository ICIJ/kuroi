"""kuroi backups — list and garbage-collect backup sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.backup import sweep_backups
from kuroi.core.config import xdg_data_home

backups_app = typer.Typer(help="Manage the kuroi backup directory.")
console = Console()


def _default_root() -> Path:
    return xdg_data_home() / "kuroi" / "backups"


@backups_app.command("list")
def list_(
    root: Path | None = typer.Option(
        None, "--root", help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups]."
    ),
) -> None:
    """List backup sessions with their ages."""
    if root is None:
        root = _default_root()
    if not root.is_dir():
        console.print(f"  No backups directory at {root}.")
        return
    entries = sorted(p for p in root.iterdir() if p.is_dir())
    if not entries:
        console.print("  (no backups)")
        return
    now = datetime.now(UTC)
    for entry in entries:
        manifest = entry / "manifest.json"
        if not manifest.exists():
            continue
        data = json.loads(manifest.read_text())
        ts_str = data.get("timestamp", entry.name)
        try:
            # The timestamp prefix is the first 20 chars of the dir name.
            ts = datetime.strptime(ts_str[:20], "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
            age = now - ts
            age_label = f"{int(age.total_seconds() / 3600)}h ago"
        except ValueError:
            age_label = "(unknown age)"
        console.print(f"  {entry.name}  {age_label}  {data.get('original_path', '?')}")


@backups_app.command("gc")
def gc(
    root: Path | None = typer.Option(
        None, "--root", help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups]."
    ),
    max_age: int = typer.Option(24, "--max-age", help="Hours; 0 = keep all."),
) -> None:
    """Prune backups older than `--max-age` hours."""
    if root is None:
        root = _default_root()
    pruned = sweep_backups(root, retention_hours=max_age)
    console.print(f"  Pruned {pruned} backup{'s' if pruned != 1 else ''}.")
