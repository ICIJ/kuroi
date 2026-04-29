"""Per-output advisory lock files for concurrent kuroi runs.

`output_lock(target)` creates `<target>.kuroi.lock` via `O_CREAT|O_EXCL`. If
the file exists, raises `LockHeldError` — the caller surfaces a clear message
instructing the user to delete the lock file if it's stale.

The lock is removed on clean exit AND on uncaught exceptions inside the
context. Stale locks (process killed before cleanup) are NOT auto-removed:
they are a real signal of a prior crash and the user clears them deliberately.
"""

from __future__ import annotations

import contextlib
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType


class LockHeldError(Exception):
    """The output lock could not be acquired because someone else holds it."""

    def __init__(self, target: Path, lock_path: Path) -> None:
        super().__init__(
            f"another kuroi run is writing to {target}; "
            f"if that's wrong, delete {lock_path}"
        )
        self.target = target
        self.lock_path = lock_path


@contextmanager
def output_lock(target: Path) -> Iterator[None]:
    """Acquire a `<target>.kuroi.lock` file for the duration of the context."""
    lock_path = target.with_suffix(target.suffix + ".kuroi.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise LockHeldError(target, lock_path) from exc
    os.close(fd)

    def _cleanup(*_args: object) -> None:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()

    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)

    def _on_int(signum: int, frame: FrameType | None) -> None:
        _cleanup()
        if callable(prev_int):
            prev_int(signum, frame)

    def _on_term(signum: int, frame: FrameType | None) -> None:
        _cleanup()
        if callable(prev_term):
            prev_term(signum, frame)

    signal.signal(signal.SIGINT, _on_int)
    signal.signal(signal.SIGTERM, _on_term)

    try:
        yield
    finally:
        _cleanup()
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
