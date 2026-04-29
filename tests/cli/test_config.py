import json
from pathlib import Path

from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_config_refresh_pricing_writes_user_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    src = tmp_path / "new-pricing.json"
    src.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-05-01",
        "providers": {
            "anthropic": {
                "claude-opus-4-7": {"input_per_million": 99.0, "output_per_million": 199.0}
            }
        },
    }))

    result = runner.invoke(app, ["config", "refresh-pricing", "--from", str(src)])

    assert result.exit_code == 0
    user_file = tmp_path / "xdg-data" / "kuroi" / "pricing.json"
    assert user_file.exists()
    data = json.loads(user_file.read_text())
    assert data["providers"]["anthropic"]["claude-opus-4-7"]["input_per_million"] == 99.0


def test_config_refresh_pricing_rejects_invalid_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    src = tmp_path / "bad.json"
    src.write_text("not json at all")

    result = runner.invoke(app, ["config", "refresh-pricing", "--from", str(src)])

    assert result.exit_code != 0
