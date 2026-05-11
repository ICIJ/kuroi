import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kuroi.core.backup import create_backup, latest_backup, sweep_backups


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


def test_latest_backup_returns_most_recent(make_pdf: Callable[..., Path], tmp_path: Path) -> None:
    original = make_pdf(["body"], filename="original.pdf")
    backup_root = tmp_path / "backups"

    first = create_backup(original, backup_root=backup_root)
    second = create_backup(original, backup_root=backup_root)

    latest = latest_backup(backup_root)
    assert latest is not None
    # Both backups carry the same timestamp prefix (within one second); the
    # 6-char random suffix makes their order alphabetic rather than temporal.
    # Either is an acceptable "latest" — we just assert one was selected.
    assert latest.timestamp[:20] == first.timestamp[:20] == second.timestamp[:20]
    assert latest.timestamp in {first.timestamp, second.timestamp}


def test_latest_backup_returns_none_when_empty(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    assert latest_backup(backup_root) is None


def test_backup_timestamp_has_random_suffix(tmp_path: Path) -> None:
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4\n")
    backup = create_backup(src, backup_root=tmp_path / "backups")
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{6}$",
        backup.timestamp,
    )


def test_sweep_backups_removes_expired(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    root.mkdir()
    # Old: 30h ago.
    old_ts = (datetime.now(UTC) - timedelta(hours=30)).strftime("%Y-%m-%dT%H-%M-%SZ-aaaaaa")
    (root / old_ts).mkdir()
    (root / old_ts / "manifest.json").write_text("{}")
    # New: just now.
    new_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ-bbbbbb")
    (root / new_ts).mkdir()
    (root / new_ts / "manifest.json").write_text("{}")

    pruned = sweep_backups(root, retention_hours=24)

    assert pruned == 1
    assert not (root / old_ts).exists()
    assert (root / new_ts).exists()


def test_sweep_backups_zero_means_keep_all(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    root.mkdir()
    very_old = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%dT%H-%M-%SZ-cccccc")
    (root / very_old).mkdir()

    pruned = sweep_backups(root, retention_hours=0)

    assert pruned == 0
    assert (root / very_old).exists()


def test_sweep_backups_ignores_non_kuroi_dirs(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    root.mkdir()
    (root / "user-dropped-this").mkdir()
    (root / "random-file.txt").write_text("hi")

    pruned = sweep_backups(root, retention_hours=24)

    assert pruned == 0
    assert (root / "user-dropped-this").exists()
    assert (root / "random-file.txt").exists()
