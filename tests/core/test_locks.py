from pathlib import Path

import pytest

from kuroi.core.locks import LockHeldError, output_lock


def test_output_lock_creates_and_removes_lock_file(tmp_path: Path):
    target = tmp_path / "out.pdf"
    lock_path = target.with_suffix(target.suffix + ".kuroi.lock")
    with output_lock(target):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_output_lock_refuses_when_held(tmp_path: Path):
    target = tmp_path / "out.pdf"
    lock_path = target.with_suffix(target.suffix + ".kuroi.lock")
    lock_path.write_text("")  # simulate stale or held lock
    with pytest.raises(LockHeldError), output_lock(target):
        pass
    # We did not remove the existing lock — only the holder removes it.
    assert lock_path.exists()


def test_output_lock_removes_on_exception(tmp_path: Path):
    target = tmp_path / "out.pdf"
    lock_path = target.with_suffix(target.suffix + ".kuroi.lock")
    with pytest.raises(RuntimeError), output_lock(target):
        assert lock_path.exists()
        raise RuntimeError("simulated failure")
    assert not lock_path.exists()
