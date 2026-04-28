"""Backup directory management for kuroi undo.

Layout:
    <root>/
        2026-04-29T14-22-13Z/
            manifest.json          # original_path, copy_path, timestamp
            <basename>.pdf         # the original file
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Backup:
    timestamp: str
    copy_path: Path
    manifest_path: Path
    original_path: Path


def create_backup(original: Path, *, backup_root: Path) -> Backup:
    """Copy `original` into a fresh timestamped subdirectory of `backup_root`."""
    ts = _next_timestamp(backup_root)
    session_dir = backup_root / ts
    session_dir.mkdir(parents=True, exist_ok=False)

    copy_path = session_dir / original.name
    shutil.copy2(original, copy_path)

    manifest_path = session_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "original_path": str(original),
                "copy_path": str(copy_path),
                "timestamp": ts,
            }
        )
    )
    return Backup(
        timestamp=ts,
        copy_path=copy_path,
        manifest_path=manifest_path,
        original_path=original,
    )


def latest_backup(backup_root: Path) -> Backup | None:
    """Return the most recent backup in `backup_root`, or None."""
    if not backup_root.is_dir():
        return None
    sessions = [p for p in backup_root.iterdir() if p.is_dir()]
    if not sessions:
        return None
    sessions.sort(key=lambda p: p.name)
    latest_dir = sessions[-1]
    manifest_path = latest_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    data = json.loads(manifest_path.read_text())
    return Backup(
        timestamp=data["timestamp"],
        copy_path=Path(data["copy_path"]),
        manifest_path=manifest_path,
        original_path=Path(data["original_path"]),
    )


def _next_timestamp(backup_root: Path) -> str:
    """Generate a timestamp that is unique within the backup root.

    If the current second already has a backup directory, wait briefly so the
    sort order in latest_backup remains correct.
    """
    while True:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        candidate = backup_root / ts
        if not candidate.exists():
            return ts
        time.sleep(0.01)
