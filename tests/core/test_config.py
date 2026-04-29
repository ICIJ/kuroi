"""Tests for kuroi.core.config — types, IO, and resolution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from kuroi.core.config import (
    Config,
    ConfigError,
    ConfigOverrides,
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
