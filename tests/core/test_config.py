"""Tests for kuroi.core.config — types, IO, and resolution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from kuroi.core.config import (
    DEFAULT_RETRY_POLICY,
    Config,
    ConfigError,
    ConfigOverrides,
    RetryPolicy,
    load_config_file,
    resolve_config,
    write_config_file,
    xdg_config_home,
    xdg_data_home,
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


def test_xdg_data_home_honors_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom-xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(target))
    assert xdg_data_home() == target


def test_xdg_data_home_falls_back_to_home_local_share(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert xdg_data_home() == tmp_path / ".local" / "share"


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
        'provider = "ollama"\nmodel = "llama3.1:8b"\n\n[ollama]\nurl = "http://localhost:11434"\n'
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
    cfg_a = Config(
        provider="anthropic", model="claude-opus-4-7", ollama_url="http://localhost:11434"
    )
    write_config_file(path, cfg_a)
    original = path.read_text()

    cfg_b = Config(provider="ollama", model="llama3.1:8b", ollama_url="http://localhost:11434")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError):
        write_config_file(path, cfg_b)
    assert path.read_text() == original
    # write_config_file unlinks the .tmp file in its except block before re-raising
    assert not (path.parent / (path.name + ".tmp")).exists()


# ---------------------------------------------------------------------------
# resolve_config tests
# ---------------------------------------------------------------------------


def _file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_resolve_uses_built_in_defaults_when_nothing_set(tmp_path: Path) -> None:
    cfg = resolve_config(ConfigOverrides(), env={}, file_path=tmp_path / "missing.toml")
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-opus-4-7"
    assert cfg.ollama_url == "http://localhost:11434"


def test_resolve_reads_provider_and_model_from_file(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        'provider = "ollama"\nmodel = "llama3.1:8b"\n\n[ollama]\nurl = "http://h:1"\n',
    )
    cfg = resolve_config(ConfigOverrides(), env={}, file_path=path)
    assert cfg.provider == "ollama"
    assert cfg.model == "llama3.1:8b"
    assert cfg.ollama_url == "http://h:1"


def test_resolve_env_beats_file(tmp_path: Path) -> None:
    path = _file(tmp_path, 'provider = "anthropic"\nmodel = "claude-opus-4-7"\n')
    cfg = resolve_config(
        ConfigOverrides(),
        env={"KUROI_PROVIDER": "ollama", "KUROI_MODEL": "llama3.1:8b"},
        file_path=path,
    )
    assert cfg.provider == "ollama"
    assert cfg.model == "llama3.1:8b"


def test_resolve_cli_beats_env(tmp_path: Path) -> None:
    cfg = resolve_config(
        ConfigOverrides(provider="anthropic", model="claude-haiku-4-5-20251001"),
        env={"KUROI_PROVIDER": "ollama", "KUROI_MODEL": "llama3.1:8b"},
        file_path=tmp_path / "missing.toml",
    )
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-haiku-4-5-20251001"


def test_resolve_per_key_mixing(tmp_path: Path) -> None:
    """provider from file, model from CLI, ollama_url from env."""
    path = _file(tmp_path, 'provider = "ollama"\nmodel = "old-model"\n')
    cfg = resolve_config(
        ConfigOverrides(model="llama3.1:70b"),
        env={"KUROI_OLLAMA_URL": "http://env-host:11434"},
        file_path=path,
    )
    assert cfg.provider == "ollama"
    assert cfg.model == "llama3.1:70b"
    assert cfg.ollama_url == "http://env-host:11434"


def test_resolve_kuroi_ollama_url_env(tmp_path: Path) -> None:
    cfg = resolve_config(
        ConfigOverrides(),
        env={"KUROI_OLLAMA_URL": "http://example:99"},
        file_path=tmp_path / "missing.toml",
    )
    assert cfg.ollama_url == "http://example:99"


def test_resolve_unknown_provider_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        resolve_config(
            ConfigOverrides(provider="bogus"),
            env={},
            file_path=tmp_path / "missing.toml",
        )
    assert "bogus" in str(exc.value)


def test_resolve_ollama_with_no_model_raises(tmp_path: Path) -> None:
    """Ollama has no built-in default model; resolution must require one."""
    with pytest.raises(ConfigError) as exc:
        resolve_config(
            ConfigOverrides(provider="ollama"),
            env={},
            file_path=tmp_path / "missing.toml",
        )
    msg = str(exc.value)
    assert "Ollama" in msg or "ollama" in msg
    assert "model" in msg.lower()


def test_resolve_anthropic_with_no_model_uses_default(tmp_path: Path) -> None:
    cfg = resolve_config(
        ConfigOverrides(provider="anthropic"),
        env={},
        file_path=tmp_path / "missing.toml",
    )
    assert cfg.model == "claude-opus-4-7"


def test_resolve_rejects_wrong_type_in_file(tmp_path: Path) -> None:
    path = _file(tmp_path, "provider = 7\n")
    with pytest.raises(ConfigError) as exc:
        resolve_config(ConfigOverrides(), env={}, file_path=path)
    assert "provider" in str(exc.value)
    assert "string" in str(exc.value)


def test_resolve_rejects_wrong_type_in_nested_table(tmp_path: Path) -> None:
    path = _file(tmp_path, "[ollama]\nurl = 7\n")
    with pytest.raises(ConfigError) as exc:
        resolve_config(ConfigOverrides(), env={}, file_path=path)
    assert "ollama.url" in str(exc.value) or "url" in str(exc.value)
    assert "string" in str(exc.value)


def test_resolve_invalid_toml_raises(tmp_path: Path) -> None:
    path = _file(tmp_path, "this is not toml ===\n")
    with pytest.raises(ConfigError):
        resolve_config(ConfigOverrides(), env={}, file_path=path)


def test_config_audit_include_text_default_false(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('provider = "anthropic"\nmodel = "m"\n')
    config = resolve_config(
        ConfigOverrides(),
        env={},
        file_path=cfg_file,
    )
    assert config.audit_include_text is False


def test_config_audit_include_text_from_file(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('provider = "anthropic"\nmodel = "m"\n\n[audit]\ninclude_text = true\n')
    config = resolve_config(
        ConfigOverrides(),
        env={},
        file_path=cfg_file,
    )
    assert config.audit_include_text is True


def test_config_backup_retention_default_24(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('provider = "anthropic"\nmodel = "m"\n')
    config = resolve_config(ConfigOverrides(), env={}, file_path=cfg)
    assert config.backup_retention_hours == 24


def test_config_backup_retention_zero_means_keep_forever(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('provider = "anthropic"\nmodel = "m"\n[backup]\nretention_hours = 0\n')
    config = resolve_config(ConfigOverrides(), env={}, file_path=cfg)
    assert config.backup_retention_hours == 0


def test_config_backup_retention_negative_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('provider = "anthropic"\nmodel = "m"\n[backup]\nretention_hours = -1\n')
    with pytest.raises(ConfigError, match="positive integer or 0"):
        resolve_config(ConfigOverrides(), env={}, file_path=cfg)


# ---------------------------------------------------------------------------
# RetryPolicy tests
# ---------------------------------------------------------------------------


def test_retry_policy_default_schedule_matches_legacy_constants() -> None:
    """The default policy reproduces the historical (2.0, 4.0) schedule."""
    assert DEFAULT_RETRY_POLICY.max_retries == 2
    assert DEFAULT_RETRY_POLICY.backoff == 2.0
    assert DEFAULT_RETRY_POLICY.backoff_multiplier == 2.0
    assert DEFAULT_RETRY_POLICY.schedule() == (2.0, 4.0)


def test_retry_policy_zero_retries_yields_empty_schedule() -> None:
    assert RetryPolicy(max_retries=0, backoff=2.0, backoff_multiplier=2.0).schedule() == ()


def test_retry_policy_multiplier_one_yields_fixed_delay() -> None:
    schedule = RetryPolicy(max_retries=3, backoff=5.0, backoff_multiplier=1.0).schedule()
    assert schedule == (5.0, 5.0, 5.0)


def test_retry_policy_exponential_growth() -> None:
    schedule = RetryPolicy(max_retries=4, backoff=1.0, backoff_multiplier=3.0).schedule()
    assert schedule == (1.0, 3.0, 9.0, 27.0)


def test_retry_policy_is_frozen() -> None:
    policy = RetryPolicy(max_retries=2, backoff=2.0, backoff_multiplier=2.0)
    with pytest.raises(FrozenInstanceError):
        policy.max_retries = 5  # type: ignore[misc]


def test_config_retry_defaults_to_default_policy(tmp_path: Path) -> None:
    cfg = resolve_config(ConfigOverrides(), env={}, file_path=tmp_path / "missing.toml")
    assert cfg.retry == DEFAULT_RETRY_POLICY


def test_config_overrides_retry_fields_default_to_none() -> None:
    overrides = ConfigOverrides()
    assert overrides.retry_max is None
    assert overrides.retry_backoff is None
    assert overrides.retry_backoff_multiplier is None


def test_resolve_retry_from_toml(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        'provider = "anthropic"\nmodel = "m"\n\n'
        "[retry]\nmax_retries = 5\nbackoff = 1.5\nbackoff_multiplier = 3.0\n",
    )
    cfg = resolve_config(ConfigOverrides(), env={}, file_path=path)
    assert cfg.retry.max_retries == 5
    assert cfg.retry.backoff == 1.5
    assert cfg.retry.backoff_multiplier == 3.0


def test_resolve_retry_partial_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    """A `[retry]` table that sets only one key still produces a complete policy."""
    path = _file(
        tmp_path,
        'provider = "anthropic"\nmodel = "m"\n\n[retry]\nmax_retries = 7\n',
    )
    cfg = resolve_config(ConfigOverrides(), env={}, file_path=path)
    assert cfg.retry.max_retries == 7
    assert cfg.retry.backoff == 2.0  # built-in default
    assert cfg.retry.backoff_multiplier == 2.0


def test_resolve_retry_rejects_negative_max_retries(tmp_path: Path) -> None:
    path = _file(tmp_path, 'provider = "anthropic"\nmodel = "m"\n\n[retry]\nmax_retries = -1\n')
    with pytest.raises(ConfigError, match="max_retries"):
        resolve_config(ConfigOverrides(), env={}, file_path=path)


def test_resolve_retry_rejects_non_int_max_retries(tmp_path: Path) -> None:
    path = _file(tmp_path, 'provider = "anthropic"\nmodel = "m"\n\n[retry]\nmax_retries = 2.5\n')
    with pytest.raises(ConfigError, match="max_retries"):
        resolve_config(ConfigOverrides(), env={}, file_path=path)


def test_resolve_retry_rejects_bool_max_retries(tmp_path: Path) -> None:
    """bool is an int subclass in Python; explicitly reject it."""
    path = _file(tmp_path, 'provider = "anthropic"\nmodel = "m"\n\n[retry]\nmax_retries = true\n')
    with pytest.raises(ConfigError, match="max_retries"):
        resolve_config(ConfigOverrides(), env={}, file_path=path)


def test_resolve_retry_rejects_negative_backoff(tmp_path: Path) -> None:
    path = _file(tmp_path, 'provider = "anthropic"\nmodel = "m"\n\n[retry]\nbackoff = -1.0\n')
    with pytest.raises(ConfigError, match="backoff"):
        resolve_config(ConfigOverrides(), env={}, file_path=path)


def test_resolve_retry_rejects_multiplier_below_one(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        'provider = "anthropic"\nmodel = "m"\n\n[retry]\nbackoff_multiplier = 0.5\n',
    )
    with pytest.raises(ConfigError, match="backoff_multiplier"):
        resolve_config(ConfigOverrides(), env={}, file_path=path)


def test_resolve_retry_rejects_non_table(tmp_path: Path) -> None:
    path = _file(tmp_path, 'provider = "anthropic"\nmodel = "m"\nretry = "fast"\n')
    with pytest.raises(ConfigError, match="retry"):
        resolve_config(ConfigOverrides(), env={}, file_path=path)
