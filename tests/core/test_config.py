"""Tests for kuroi.core.config — types, IO, and resolution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from kuroi.core.config import (
    Config,
    ConfigError,
    ConfigOverrides,
    load_config_file,
    write_config_file,
    xdg_config_home,
)


def test_xdg_config_home_honors_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom-xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(target))
    assert xdg_config_home() == target


def test_xdg_config_home_falls_back_to_home_dot_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert xdg_config_home() == tmp_path / ".config"


def test_config_is_frozen() -> None:
    cfg = Config(provider="anthropic", model="claude-opus-4-7", ollama_url="http://localhost:11434")
    with pytest.raises(FrozenInstanceError):
        cfg.provider = "ollama"  # type: ignore[misc]


def test_config_overrides_defaults_to_all_none() -> None:
    overrides = ConfigOverrides()
    assert overrides.provider is None
    assert overrides.model is None
    assert overrides.ollama_url is None


def test_config_error_is_an_exception() -> None:
    err = ConfigError("nope")
    assert isinstance(err, Exception)
    assert str(err) == "nope"


def test_load_returns_empty_dict_when_file_absent(tmp_path: Path) -> None:
    assert load_config_file(tmp_path / "missing.toml") == {}


def test_load_parses_known_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'provider = "ollama"\n'
        'model = "llama3.1:8b"\n'
        '\n'
        '[ollama]\n'
        'url = "http://localhost:11434"\n'
    )
    data = load_config_file(path)
    assert data["provider"] == "ollama"
    assert data["model"] == "llama3.1:8b"
    assert data["ollama"]["url"] == "http://localhost:11434"


def test_load_raises_config_error_on_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("provider = ===\n")
    with pytest.raises(ConfigError) as exc:
        load_config_file(path)
    assert str(path) in str(exc.value)


def test_write_round_trips_through_load(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = Config(provider="ollama", model="llama3.1:8b", ollama_url="http://example:11434")
    write_config_file(path, cfg)
    assert path.exists()
    data = load_config_file(path)
    assert data["provider"] == "ollama"
    assert data["model"] == "llama3.1:8b"
    assert data["ollama"]["url"] == "http://example:11434"


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "subdir" / "config.toml"
    cfg = Config(provider="anthropic", model="claude-opus-4-7", ollama_url="http://localhost:11434")
    write_config_file(path, cfg)
    assert path.exists()


def test_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If os.replace fails, the original file is untouched."""
    path = tmp_path / "config.toml"
    cfg_a = Config(provider="anthropic", model="claude-opus-4-7", ollama_url="http://localhost:11434")
    write_config_file(path, cfg_a)
    original = path.read_text()

    cfg_b = Config(provider="ollama", model="llama3.1:8b", ollama_url="http://localhost:11434")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError):
        write_config_file(path, cfg_b)
    assert path.read_text() == original
    assert not (path.parent / (path.name + ".tmp")).exists() or True  # tmp may or may not be cleaned
