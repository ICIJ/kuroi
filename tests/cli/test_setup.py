"""Tests for the interactive `kuroi setup` command."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from kuroi.cli import app


def _xdg_path(tmp_path: Path) -> Path:
    return tmp_path / "xdg-config" / "kuroi" / "config.toml"


def test_setup_first_run_anthropic_writes_config(tmp_path: Path) -> None:
    """User selects Anthropic and a curated model, file is written."""
    runner = CliRunner()
    # Inputs: provider="1" (anthropic), model index "1" (first curated entry).
    result = runner.invoke(app, ["setup"], input="1\n1\n")
    assert result.exit_code == 0, result.stdout
    cfg_path = _xdg_path(tmp_path)
    assert cfg_path.is_file()
    body = cfg_path.read_text()
    assert 'provider = "anthropic"' in body
    assert "claude-" in body


def test_setup_anthropic_warns_when_api_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["setup"], input="1\n1\n")
    assert result.exit_code == 0, result.stdout
    assert "ANTHROPIC_API_KEY" in result.stdout
    # Soft warning, not failure.


def test_setup_re_run_uses_existing_values_as_defaults(tmp_path: Path) -> None:
    """Pressing Enter at every prompt keeps the existing values."""
    cfg_path = _xdg_path(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        'provider = "anthropic"\n'
        'model = "claude-opus-4-7"\n\n'
        '[ollama]\nurl = "http://localhost:11434"\n'
    )
    runner = CliRunner()
    # Empty inputs (just Enter) → keep defaults.
    result = runner.invoke(app, ["setup"], input="\n\n")
    assert result.exit_code == 0, result.stdout
    body = cfg_path.read_text()
    assert 'provider = "anthropic"' in body
    assert 'model = "claude-opus-4-7"' in body


def test_setup_re_run_preserves_non_default_model(tmp_path: Path) -> None:
    """Pressing Enter on a model that isn't index 0 still keeps it."""
    cfg_path = _xdg_path(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        'provider = "anthropic"\n'
        'model = "claude-haiku-4-5-20251001"\n\n'
        '[ollama]\nurl = "http://localhost:11434"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["setup"], input="\n\n")
    assert result.exit_code == 0, result.stdout
    body = cfg_path.read_text()
    assert 'model = "claude-haiku-4-5-20251001"' in body


def test_setup_ollama_probe_success_populates_model_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When /api/tags returns models, the picker lists them."""
    from kuroi.cli import setup as setup_module

    def _probe(_url: str) -> list[str] | None:
        return ["llama3.1:8b", "mistral:7b"]

    monkeypatch.setattr(setup_module, "probe_ollama_models", _probe)

    runner = CliRunner()
    # Inputs: provider="2" (ollama), URL Enter (default localhost), model "1".
    result = runner.invoke(app, ["setup"], input="2\n\n1\n")
    assert result.exit_code == 0, result.stdout
    assert "llama3.1:8b" in result.stdout
    assert "mistral:7b" in result.stdout
    body = _xdg_path(tmp_path).read_text()
    assert 'provider = "ollama"' in body
    assert 'model = "llama3.1:8b"' in body


def test_setup_ollama_probe_failure_falls_back_to_free_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When /api/tags is unreachable, user types a model name."""
    from kuroi.cli import setup as setup_module

    def _probe(_url: str) -> list[str] | None:
        return None  # unreachable

    monkeypatch.setattr(setup_module, "probe_ollama_models", _probe)

    runner = CliRunner()
    # Inputs: provider="2", URL Enter, free-text model "llama3.1:70b".
    result = runner.invoke(app, ["setup"], input="2\n\nllama3.1:70b\n")
    assert result.exit_code == 0, result.stdout
    assert "unreachable" in result.stdout.lower() or "warning" in result.stdout.lower()
    body = _xdg_path(tmp_path).read_text()
    assert 'model = "llama3.1:70b"' in body


def test_setup_reports_config_error_on_corrupt_file(tmp_path: Path) -> None:
    """A corrupt config file is surfaced as exit 2 with a red message, not a stack trace."""
    cfg_path = _xdg_path(tmp_path)
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("provider = ===\n")  # malformed TOML
    runner = CliRunner()
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 2
    assert "Config error" in result.stdout
    assert "Traceback" not in result.stdout


def test_probe_ollama_models_handles_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct test of the probe helper using a stub httpx.get."""
    from kuroi.cli import setup as setup_module

    def _bad_get(*_a: Any, **_kw: Any) -> Any:
        raise httpx.ConnectError("nope")

    monkeypatch.setattr("httpx.get", _bad_get)
    assert setup_module.probe_ollama_models("http://localhost:11434") is None


def test_probe_ollama_models_returns_names_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe parses the /api/tags response."""
    from kuroi.cli import setup as setup_module

    class _R:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]}

    def _good_get(*_a: Any, **_kw: Any) -> _R:
        return _R()

    monkeypatch.setattr("httpx.get", _good_get)
    assert setup_module.probe_ollama_models("http://localhost:11434") == ["llama3.1:8b", "mistral:7b"]


def test_setup_offers_claude_cli_option(monkeypatch, tmp_path) -> None:
    """When the user picks 3, the config gets `provider = "claude-cli"`."""
    from kuroi.cli import setup as setup_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    answers = iter(["3", "1"])  # provider=3 (claude-cli), model=1 (opus)

    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *a, **kw: next(answers))
    # Stub the probe to succeed so setup writes the config.
    monkeypatch.setattr(setup_mod, "probe_claude_cli", lambda cli_path=None: True)

    setup_mod.setup()

    cfg = (tmp_path / "kuroi" / "config.toml").read_text()
    assert 'provider = "claude-cli"' in cfg
    assert 'model = "claude-opus-4-7"' in cfg


def test_setup_aborts_when_claude_cli_probe_fails(monkeypatch, tmp_path) -> None:
    import pytest
    import typer

    from kuroi.cli import setup as setup_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    answers = iter(["3", "1"])
    monkeypatch.setattr(setup_mod.typer, "prompt", lambda *a, **kw: next(answers))
    monkeypatch.setattr(setup_mod, "probe_claude_cli", lambda cli_path=None: False)

    with pytest.raises(typer.Exit):
        setup_mod.setup()
    cfg_path = tmp_path / "kuroi" / "config.toml"
    assert not cfg_path.exists()
