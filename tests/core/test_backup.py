import json
from collections.abc import Callable
from pathlib import Path

from kuroi.core.backup import create_backup, latest_backup


def test_create_backup_copies_original_and_writes_manifest(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    original = make_pdf(["body"], filename="original.pdf")
    backup_root = tmp_path / "backups"

    bak = create_backup(original, backup_root=backup_root)

    assert bak.copy_path.is_file()
    assert bak.copy_path.read_bytes() == original.read_bytes()
    manifest = json.loads(bak.manifest_path.read_text())
    assert manifest["original_path"] == str(original)
    assert manifest["copy_path"] == str(bak.copy_path)
    assert manifest["timestamp"] == bak.timestamp


def test_latest_backup_returns_most_recent(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    original = make_pdf(["body"], filename="original.pdf")
    backup_root = tmp_path / "backups"

    first = create_backup(original, backup_root=backup_root)
    second = create_backup(original, backup_root=backup_root)

    latest = latest_backup(backup_root)
    assert latest is not None
    assert latest.timestamp >= first.timestamp
    assert latest.timestamp == second.timestamp


def test_latest_backup_returns_none_when_empty(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    assert latest_backup(backup_root) is None
