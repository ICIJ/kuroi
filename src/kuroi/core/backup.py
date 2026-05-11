"""Backup directory management for kuroi undo.

Layout:
    <root>/
        2026-04-29T14-22-13Z/
            manifest.json          # original_path, copy_path, timestamp
            <basename>.pdf         # the original file
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_BACKUP_DIR_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)(-[0-9a-f]{6})?$")


@dataclass(frozen=True)
class Backup:
    timestamp: str
    copy_path: Path
    manifest_path: Path
    original_path: Path


def create_backup(original: Path, *, backup_root: Path) -> Backup:
    """Copy `original` into a fresh timestamped subdirectory of `backup_root`."""
    ts = session_timestamp()
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


def session_timestamp() -> str:
    """Generate a session timestamp with a 6-char random suffix.

    The suffix avoids collisions across concurrent runs even within the same
    second; the lazy sweep parses the leading timestamp and ignores the suffix.
    """
    suffix = secrets.token_hex(3)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{ts}-{suffix}"


def sweep_backups(root: Path, *, retention_hours: int) -> int:
    """Remove backup subdirectories older than `retention_hours`.

    `retention_hours = 0` disables pruning (legal-hold mode).
    Entries whose names don't match the kuroi timestamp pattern are left alone.
    Returns the number of pruned subdirectories.
    """
    if retention_hours == 0 or not root.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
    pruned = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        m = _BACKUP_DIR_RE.match(child.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if ts < cutoff:
            shutil.rmtree(child)
            pruned += 1
    return pruned


def find_session_by_path(backup_root: Path, target: Path) -> Backup | None:
    """Return the most recent backup whose original_path matches `target`.

    Path matching uses `Path.resolve(strict=False)` on both sides so symlinks
    and relative-vs-absolute spellings reconcile.
    """
    if not backup_root.is_dir():
        return None
    target_resolved = target.resolve(strict=False)
    candidates: list[tuple[str, Backup]] = []
    for session_dir in backup_root.iterdir():
        if not session_dir.is_dir():
            continue
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        orig = Path(data["original_path"]).resolve(strict=False)
        if orig == target_resolved:
            candidates.append(
                (
                    session_dir.name,
                    Backup(
                        timestamp=data["timestamp"],
                        copy_path=Path(data["copy_path"]),
                        manifest_path=manifest_path,
                        original_path=Path(data["original_path"]),
                    ),
                )
            )
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def find_session_by_timestamp(backup_root: Path, ts: str) -> Backup | None:
    """Return the backup whose session directory matches `ts` exactly."""
    session_dir = backup_root / ts
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return None
    return Backup(
        timestamp=data["timestamp"],
        copy_path=Path(data["copy_path"]),
        manifest_path=manifest_path,
        original_path=Path(data["original_path"]),
    )
