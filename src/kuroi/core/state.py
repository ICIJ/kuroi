"""kuroi user-state file at $XDG_STATE_HOME/kuroi/state.toml.

State is "what kuroi knows about the user" — the cloud-use acknowledgement,
the training-wheels run counter, and the total run count. Distinct from
config (which is "what the user has set"). Atomic writes; mode 0600.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class State:
    schema_version: int = 1
    cloud_acknowledged: bool = False
    cloud_acknowledged_at: str = ""
    training_wheels_remaining: int = 3
    total_runs: int = 0


def xdg_state_home() -> Path:
    """`$XDG_STATE_HOME` or `~/.local/state`."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "state"


def state_path() -> Path:
    return xdg_state_home() / "kuroi" / "state.toml"


def load_state(path: Path) -> State:
    """Load state. Returns defaults when the file is missing."""
    if not path.exists():
        return State()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return State(
        schema_version=int(raw.get("schema_version", 1)),
        cloud_acknowledged=bool(raw.get("cloud_acknowledged", False)),
        cloud_acknowledged_at=str(raw.get("cloud_acknowledged_at", "")),
        training_wheels_remaining=int(raw.get("training_wheels_remaining", 3)),
        total_runs=int(raw.get("total_runs", 0)),
    )


def write_state(path: Path, state: State) -> None:
    """Atomic write to `path` with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"schema_version = {state.schema_version}\n"
        f"cloud_acknowledged = {str(state.cloud_acknowledged).lower()}\n"
        f'cloud_acknowledged_at = "{state.cloud_acknowledged_at}"\n'
        f"training_wheels_remaining = {state.training_wheels_remaining}\n"
        f"total_runs = {state.total_runs}\n"
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(str(path), 0o600)
