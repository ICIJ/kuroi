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


@dataclass(frozen=True)
class RetryPolicy:
    """Retry attempts and backoff schedule for a chunked LLM call.

    `max_retries` is the count of retries *after* the initial attempt;
    total attempts = `max_retries + 1`. Setting `max_retries=0` disables retry.

    The schedule grows geometrically: the k-th retry delay (0-indexed) is
    `backoff * backoff_multiplier ** k`. A multiplier of 1.0 gives
    a fixed delay; the default `(2, 2.0, 2.0)` produces `(2.0, 4.0)` —
    the historical hardcoded schedule.
    """

    max_retries: int
    backoff: float
    backoff_multiplier: float

    def schedule(self) -> tuple[float, ...]:
        return tuple(
            self.backoff * (self.backoff_multiplier ** k)
            for k in range(self.max_retries)
        )


DEFAULT_RETRY_POLICY = RetryPolicy(max_retries=2, backoff=2.0, backoff_multiplier=2.0)


class ConfigError(Exception):
    """Raised when configuration cannot be resolved or is invalid."""


@dataclass(frozen=True)
class Config:
    """Resolved configuration. After successful `resolve_config`, every field is set."""

    provider: ProviderName
    model: str
    ollama_url: str
    audit_include_text: bool = False
    backup_retention_hours: int = 24
    retry: RetryPolicy = DEFAULT_RETRY_POLICY


@dataclass(frozen=True)
class ConfigOverrides:
    """CLI-flag values to overlay on top of env, file, and built-ins.

    Each field is `None` when the corresponding flag was not passed.
    """

    provider: str | None = None
    model: str | None = None
    ollama_url: str | None = None
    retry_max: int | None = None
    retry_backoff: float | None = None
    retry_backoff_multiplier: float | None = None


def xdg_config_home() -> Path:
    """Return the XDG config directory.

    Honors `$XDG_CONFIG_HOME` when set; otherwise falls back to `~/.config`.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def xdg_data_home() -> Path:
    """Return the XDG data directory.

    Honors `$XDG_DATA_HOME` when set; otherwise falls back to `~/.local/share`.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


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
        f"\n"
        f"[ollama]\n"
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
        raise ConfigError(f"Expected string for `{label or key}`, got {type(value).__name__}")
    return value


def _read_retry_policy(
    file_data: dict[str, Any],
    env: Mapping[str, str],
    overrides: "ConfigOverrides",
) -> "RetryPolicy":
    """Resolve the retry policy by walking CLI → env → file → built-in defaults
    independently for each of the three keys.

    Raises `ConfigError` on any invalid value (wrong type, negative, multiplier < 1).
    """
    retry_table = file_data.get("retry", {})
    if not isinstance(retry_table, dict):
        raise ConfigError(f"Expected table for `retry`, got {type(retry_table).__name__}")

    # max_retries: int >= 0
    max_retries: int = DEFAULT_RETRY_POLICY.max_retries
    if "max_retries" in retry_table:
        raw = retry_table["max_retries"]
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise ConfigError(
                f"Expected non-negative integer for `retry.max_retries`, got {raw!r}"
            )
        max_retries = raw
    if overrides.retry_max is not None:
        if overrides.retry_max < 0:
            raise ConfigError(
                f"Expected non-negative integer for `retry.max_retries`, got {overrides.retry_max!r}"
            )
        max_retries = overrides.retry_max

    # backoff: float >= 0
    backoff: float = DEFAULT_RETRY_POLICY.backoff
    if "backoff" in retry_table:
        raw = retry_table["backoff"]
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw < 0:
            raise ConfigError(
                f"Expected non-negative number for `retry.backoff`, got {raw!r}"
            )
        backoff = float(raw)
    if overrides.retry_backoff is not None:
        if overrides.retry_backoff < 0:
            raise ConfigError(
                f"Expected non-negative number for `retry.backoff`, got {overrides.retry_backoff!r}"
            )
        backoff = float(overrides.retry_backoff)

    # backoff_multiplier: float >= 1.0
    multiplier: float = DEFAULT_RETRY_POLICY.backoff_multiplier
    if "backoff_multiplier" in retry_table:
        raw = retry_table["backoff_multiplier"]
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw < 1.0:
            raise ConfigError(
                f"Expected number >= 1.0 for `retry.backoff_multiplier`, got {raw!r}"
            )
        multiplier = float(raw)
    if overrides.retry_backoff_multiplier is not None:
        if overrides.retry_backoff_multiplier < 1.0:
            raise ConfigError(
                f"Expected number >= 1.0 for `retry.backoff_multiplier`, got {overrides.retry_backoff_multiplier!r}"
            )
        multiplier = float(overrides.retry_backoff_multiplier)

    return RetryPolicy(max_retries=max_retries, backoff=backoff, backoff_multiplier=multiplier)


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

    model = overrides.model or env.get("KUROI_MODEL") or _read_string(file_data, "model")
    if model is None:
        if provider == "anthropic":
            model = DEFAULT_ANTHROPIC_MODEL
        else:
            raise ConfigError("No Ollama model configured. Run `kuroi setup` or pass --model.")

    ollama_url = (
        overrides.ollama_url
        or env.get("KUROI_OLLAMA_URL")
        or _read_string(file_data, "ollama.url")
        or DEFAULT_OLLAMA_URL
    )

    audit_table = file_data.get("audit", {})
    if not isinstance(audit_table, dict):
        raise ConfigError(f"Expected table for `audit`, got {type(audit_table).__name__}")
    audit_include_text_raw = audit_table.get("include_text", False)
    if not isinstance(audit_include_text_raw, bool):
        raise ConfigError(
            f"Expected boolean for `audit.include_text`, got {type(audit_include_text_raw).__name__}"
        )

    backup_table = file_data.get("backup", {})
    if not isinstance(backup_table, dict):
        raise ConfigError(f"Expected table for `backup`, got {type(backup_table).__name__}")
    backup_retention_raw = backup_table.get("retention_hours", 24)
    if (
        not isinstance(backup_retention_raw, int)
        or isinstance(backup_retention_raw, bool)
        or backup_retention_raw < 0
    ):
        raise ConfigError(
            "kuroi requires backups for in-place edits; set retention_hours "
            "to a positive integer or 0"
        )

    return Config(
        provider=provider,
        model=model,
        ollama_url=ollama_url,
        audit_include_text=audit_include_text_raw,
        backup_retention_hours=backup_retention_raw,
        retry=_read_retry_policy(file_data, env, overrides),
    )
