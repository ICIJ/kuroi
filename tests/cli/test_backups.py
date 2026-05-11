from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_backups_list_shows_each_session(tmp_path: Path) -> None:
    bdir = tmp_path / "backups"
    bdir.mkdir()
    name = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ-abcdef")
    (bdir / name).mkdir()
    (bdir / name / "manifest.json").write_text(
        '{"original_path": "/x.pdf", "copy_path": "y", "timestamp": "' + name + '"}'
    )

    result = runner.invoke(app, ["backups", "list", "--root", str(bdir)])

    assert result.exit_code == 0
    assert name in result.stdout


def test_backups_gc_prunes_old(tmp_path: Path) -> None:
    bdir = tmp_path / "backups"
    bdir.mkdir()
    old = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%dT%H-%M-%SZ-aaaaaa")
    (bdir / old).mkdir()
    (bdir / old / "manifest.json").write_text("{}")

    result = runner.invoke(app, ["backups", "gc", "--root", str(bdir), "--max-age", "24"])

    assert result.exit_code == 0
    assert "Pruned 1" in result.stdout
    assert not (bdir / old).exists()
