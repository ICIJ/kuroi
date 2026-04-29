import os
import stat
from pathlib import Path

import pytest

from kuroi.core.state import State, load_state, write_state, xdg_state_home


def test_xdg_state_home_uses_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "x"))
    assert xdg_state_home() == tmp_path / "x"


def test_load_state_returns_defaults_when_missing(tmp_path: Path) -> None:
    state = load_state(tmp_path / "state.toml")
    assert state.schema_version == 1
    assert state.cloud_acknowledged is False
    assert state.training_wheels_remaining == 3
    assert state.total_runs == 0


def test_write_then_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "state.toml"
    s = State(
        schema_version=1,
        cloud_acknowledged=True,
        cloud_acknowledged_at="2026-04-29T12:00:00Z",
        training_wheels_remaining=2,
        total_runs=7,
    )
    write_state(p, s)
    loaded = load_state(p)
    assert loaded == s


def test_state_file_is_mode_0600(tmp_path: Path) -> None:
    p = tmp_path / "state.toml"
    write_state(p, State(schema_version=1))
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600
