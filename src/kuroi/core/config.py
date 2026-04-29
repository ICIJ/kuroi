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
from collections.abc import Mapping
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
    audit_include_text: bool = False


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

    Assumes `config.provider`, `config.model`, and `config.ollama_url` contain
    no double-quote characters; the writer does not perform TOML escaping.
    This holds because `provider` is a `Literal`, and `model` and `ollama_url`
    are validated upstream during config resolution.
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


VALID_PROVIDERS: tuple[ProviderName, ...] = ("anthropic", "ollama")


def _read_string(data: dict[str, Any], key: str, *, label: str | None = None) -> str | None:
    """Pull a string value at `key` (dot-path supported, one level deep) from `data`.

    Returns None if the key is absent. Raises `ConfigError` if the key is present
    but not a string.
    """
    if "." in key:
        head, tail = key.split(".", 1)
        nested = data.get(head)
        if nested is None:
            return None
        if not isinstance(nested, dict):
            raise ConfigError(f"Expected table for `{head}`, got {type(nested).__name__}")
        return _read_string(nested, tail, label=label or key)
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, str):
        raise ConfigError(
            f"Expected string for `{label or key}`, got {type(value).__name__}"
        )
    return value


def resolve_config(
    overrides: ConfigOverrides,
    *,
    env: Mapping[str, str],
    file_path: Path,
) -> Config:
    """Resolve a `Config` by walking CLI → env → file → built-in defaults.

    Resolution is per-key. A `ConfigError` is raised if any key is invalid
    (unknown provider, wrong type in the file, missing required Ollama model).
    """
    file_data = load_config_file(file_path)

    provider_raw = (
        overrides.provider
        or env.get("KUROI_PROVIDER")
        or _read_string(file_data, "provider")
        or "anthropic"
    )
    if provider_raw not in VALID_PROVIDERS:
        raise ConfigError(
            f"Unknown provider: {provider_raw!r}. Expected one of {list(VALID_PROVIDERS)}."
        )
    provider: ProviderName = provider_raw

    model = (
        overrides.model
        or env.get("KUROI_MODEL")
        or _read_string(file_data, "model")
    )
    if model is None:
        if provider == "anthropic":
            model = DEFAULT_ANTHROPIC_MODEL
        else:
            raise ConfigError(
                "No Ollama model configured. Run `kuroi setup` or pass --model."
            )

    ollama_url = (
        overrides.ollama_url
        or env.get("KUROI_OLLAMA_URL")
        or _read_string(file_data, "ollama.url")
        or DEFAULT_OLLAMA_URL
    )

    audit_table = file_data.get("audit", {})
    if not isinstance(audit_table, dict):
        raise ConfigError(
            f"Expected table for `audit`, got {type(audit_table).__name__}"
        )
    audit_include_text_raw = audit_table.get("include_text", False)
    if not isinstance(audit_include_text_raw, bool):
        raise ConfigError(
            f"Expected boolean for `audit.include_text`, got {type(audit_include_text_raw).__name__}"
        )

    return Config(
        provider=provider,
        model=model,
        ollama_url=ollama_url,
        audit_include_text=audit_include_text_raw,
    )
