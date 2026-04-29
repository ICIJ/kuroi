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
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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


def load_config_file(path: Path) -> dict[str, Any]:
    """Read a kuroi config TOML file. Returns `{}` if the file does not exist.

    Raises `ConfigError` (with the file path in the message) on parse failure.
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse config at {path}: {exc}") from exc


def write_config_file(path: Path, config: Config) -> None:
    """Write `config` to `path` atomically.

    Serializes the on-disk schema (top-level `provider` and `model` plus a
    nested `[ollama]` table). Writes to `<path>.tmp` then `os.replace()` so
    a crash mid-write cannot corrupt an existing file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f'provider = "{config.provider}"\n'
        f'model = "{config.model}"\n'
        f'\n'
        f'[ollama]\n'
        f'url = "{config.ollama_url}"\n'
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
