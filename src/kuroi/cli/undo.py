"""kuroi undo — restore from backup, optionally scoped to file/pages/elements."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.backup import latest_backup, sweep_backups
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
    xdg_data_home,
)

console = Console()


def _parse_words(spec: str | None) -> tuple[int, int] | None:
    if spec is None:
        return None
    try:
        left, _, right = spec.partition("-")
        return (int(left), int(right))
    except (ValueError, AttributeError) as exc:
        raise typer.BadParameter(f"--words must be START-END (got {spec!r})") from exc


def undo(
    input: Path | None = typer.Argument(
        None,
        help="PDF whose backup should be restored. Default: most recent backup overall.",
    ),
    yes: bool = typer.Option(False, "-y", help="Skip the restore confirmation."),
    backup_dir: Path | None = typer.Option(
        None,
        "--backup-dir",
        help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups].",
    ),
    audit_dir: Path | None = typer.Option(
        None,
        "--audit-dir",
        help="Audit directory [default: $XDG_DATA_HOME/kuroi/audit].",
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Exact backup-session timestamp to undo (e.g. 2026-05-11T14-22-13Z-abc123).",
    ),
    pages: str | None = typer.Option(
        None,
        "--pages",
        help="Restrict undo to a page range (same syntax as `kuroi run --pages`).",
    ),
    page: int | None = typer.Option(None, "--page", help="Single page filter."),
    kind: str | None = typer.Option(None, "--kind", help="Finding kind filter (e.g. email)."),
    words: str | None = typer.Option(
        None,
        "--words",
        help="Word range filter on the form START-END (requires --page).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan, write nothing."),
) -> None:
    """Restore an original PDF from a backup; supports file/page/element scoping."""
    # Argument cross-checks (subsequent tasks add behavioral wiring).
    if words is not None and page is None:
        console.print("  [red]--words requires --page[/]")
        raise typer.Exit(code=2)
    _ = _parse_words(words)  # validate format even if not yet used

    if backup_dir is None:
        backup_dir = xdg_data_home() / "kuroi" / "backups"
    if audit_dir is None:
        audit_dir = xdg_data_home() / "kuroi" / "audit"

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

    # TODO(Task 8): replace this block with the locator + audit-driven flow.
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
