"""Configuration types and resolution.

`resolve_config` is a pure function that walks the precedence chain
(CLI → env → file → built-in defaults) and returns an immutable `Config`,
or raises `ConfigError` if validation fails.

The on-disk format is TOML at `$XDG_CONFIG_HOME/kuroi/config.toml`. Reading
uses stdlib `tomllib`; writing uses a tiny in-package serializer (the on-disk
shape is a flat document with one nested `[ollama]` table — no general TOML
writer needed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ProviderName = Literal["anthropic", "ollama"]
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"


class ConfigError(Exception):
    """Raised when configuration cannot be resolved or is invalid."""


@dataclass(frozen=True)
class Config:
    """Resolved configuration. After successful `resolve_config`, every field is set."""

    provider: ProviderName
    model: str
    ollama_url: str


@dataclass(frozen=True)
class ConfigOverrides:
    """CLI-flag values to overlay on top of env, file, and built-ins.

    Each field is `None` when the corresponding flag was not passed.
    """

    provider: str | None = None
    model: str | None = None
    ollama_url: str | None = None


def xdg_config_home() -> Path:
    """Return the XDG config directory.

    Honors `$XDG_CONFIG_HOME` when set; otherwise falls back to `~/.config`.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"
