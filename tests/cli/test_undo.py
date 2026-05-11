from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kuroi.cli import app
from kuroi.core.backup import create_backup


def test_undo_restores_latest_backup(make_pdf: Callable[..., Path], tmp_path: Path) -> None:
    original = make_pdf(["original body"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(original, backup_root=backup_root)

    # Now mutate the original, simulating a redaction we want to undo
    original.write_bytes(b"%PDF-1.4\n% mutated\n")

    runner = CliRunner()
    result = runner.invoke(app, ["undo", "-y", "--backup-dir", str(backup_root)])

    assert result.exit_code == 0
    assert original.read_text(errors="ignore").strip() != "%PDF-1.4\n% mutated"
    # Should have a real PDF header byte sequence again
    assert original.read_bytes().startswith(b"%PDF")


def test_undo_with_no_backup_exits_1(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    runner = CliRunner()
    result = runner.invoke(app, ["undo", "-y", "--backup-dir", str(backup_root)])
    assert result.exit_code == 1
    assert "no backup" in result.stdout.lower()


def test_undo_accepts_new_flags_without_breaking_back_compat(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    original = make_pdf(["original body"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(original, backup_root=backup_root)
    original.write_bytes(b"%PDF-1.4\n% mutated\n")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(original),
            "-y",
            "--backup-dir",
            str(backup_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert original.read_bytes().startswith(b"%PDF")


def test_undo_rejects_conflicting_words_without_page(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    original = make_pdf(["x"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(original, backup_root=backup_root)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(original),
            "-y",
            "--words",
            "1-3",
            "--backup-dir",
            str(backup_root),
        ],
    )
    assert result.exit_code == 2
    assert "--words requires --page" in result.stdout
