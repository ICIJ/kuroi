# Cluster 3 — File handling and UX implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve gaps 3.2, 3.3, and 3.4 from the spec — add `--in-place` (with mandatory backup, no `--no-backup` escape hatch), `--overwrite` with `.v<N>` collision suggestions, and output lock files for safe concurrent runs.

Gap 3.1 (drag-and-drop path normalization) is deferred to the same plan that builds guided mode; this plan does not touch it.

**Architecture:** A small helper module `core/output_resolution.py` decides the final output path given `(input, --output, --in-place, --overwrite)` and surfaces collisions as a structured exception. A new `core/locks.py` provides an `output_lock(path)` context manager using `O_CREAT | O_EXCL`, removed on clean exit and signal handlers. `cli/run.py` consumes both and gains `--in-place` / `--overwrite` flags.

**Tech Stack:** Python 3.12+, Typer, Rich, stdlib `os`, stdlib `signal`, pytest, mypy, ruff.

**Spec:** `docs/superpowers/specs/2026-04-29-design-gaps-resolution-design.md`, sections 3.2, 3.3, 3.4.

---

## File structure

**Created:**
- `src/kuroi/core/output_resolution.py` — `resolve_output_path`, `OutputCollisionError`, `suggest_versioned_name`.
- `src/kuroi/core/locks.py` — `output_lock` context manager, `LockHeldError`.
- `tests/core/test_output_resolution.py`
- `tests/core/test_locks.py`

**Modified:**
- `src/kuroi/cli/run.py` — `--in-place`, `--overwrite` flags; consume helpers.
- `tests/cli/test_run.py` — flag tests.

---

## Task 1: `.v<N>` collision suggestion helper

**Why this task exists:** Spec section 3.3 commits to a deterministic suggestion: glob `<base>.v*.pdf`, take `max(N) + 1`, fall back to `.v2`. Pure function, easy to TDD.

**Files:**
- Create: `src/kuroi/core/output_resolution.py`
- Create: `tests/core/test_output_resolution.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_output_resolution.py`:

```python
from pathlib import Path

import pytest

from kuroi.core.output_resolution import suggest_versioned_name


def test_suggest_v2_when_no_existing_version(tmp_path: Path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF")
    suggestion = suggest_versioned_name(target)
    assert suggestion == tmp_path / "report.v2.pdf"


def test_suggest_next_when_versions_exist(tmp_path: Path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF")
    (tmp_path / "report.v2.pdf").write_bytes(b"%PDF")
    (tmp_path / "report.v3.pdf").write_bytes(b"%PDF")
    suggestion = suggest_versioned_name(target)
    assert suggestion == tmp_path / "report.v4.pdf"


def test_suggest_skips_non_int_versions(tmp_path: Path):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF")
    (tmp_path / "doc.v2.pdf").write_bytes(b"%PDF")
    (tmp_path / "doc.vfoo.pdf").write_bytes(b"%PDF")  # ignored
    suggestion = suggest_versioned_name(target)
    assert suggestion == tmp_path / "doc.v3.pdf"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_output_resolution.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `suggest_versioned_name`**

Create `src/kuroi/core/output_resolution.py`:

```python
"""Output path collision handling.

`resolve_output_path` returns the final path kuroi should write to, given the
user's CLI flags. Collisions raise `OutputCollisionError` with a concrete
suggestion the CLI prints. The suggestion algorithm is `suggest_versioned_name`,
which lives separately so it can be tested without involving CLI machinery.
"""

from __future__ import annotations

import re
from pathlib import Path


_VERSIONED_RE = re.compile(r"^(?P<stem>.+)\.v(?P<n>\d+)$")


def suggest_versioned_name(target: Path) -> Path:
    """Return `<base>.v<N>.<ext>` where N is `max(existing) + 1`, or 2 if none."""
    parent = target.parent
    base = target.stem
    suffix = target.suffix
    versions: list[int] = []
    for sibling in parent.glob(f"{base}.v*{suffix}"):
        m = _VERSIONED_RE.match(sibling.stem)
        if m and m.group("stem") == base:
            try:
                versions.append(int(m.group("n")))
            except ValueError:
                continue
    next_n = max(versions) + 1 if versions else 2
    return parent / f"{base}.v{next_n}{suffix}"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/test_output_resolution.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/output_resolution.py tests/core/test_output_resolution.py
git commit -m "feat(output): add suggest_versioned_name helper"
```

---

## Task 2: `OutputCollisionError` and `resolve_output_path`

**Why this task exists:** Encapsulates the decision tree for "where do I write?" — `--in-place` wins if set; otherwise `-o` is required; `--overwrite` allows existing file. This is the unit `cli/run.py` calls.

**Files:**
- Modify: `src/kuroi/core/output_resolution.py`
- Modify: `tests/core/test_output_resolution.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_output_resolution.py`:

```python
from kuroi.core.output_resolution import (
    OutputCollisionError,
    resolve_output_path,
    OutputResolutionError,
)


def test_resolve_in_place(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    final = resolve_output_path(src, output=None, in_place=True, overwrite=False)
    assert final == src


def test_resolve_with_output_to_fresh_path(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    final = resolve_output_path(src, output=out, in_place=False, overwrite=False)
    assert final == out


def test_resolve_collision_raises_with_suggestion(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    out.write_bytes(b"%PDF")
    with pytest.raises(OutputCollisionError) as exc:
        resolve_output_path(src, output=out, in_place=False, overwrite=False)
    assert exc.value.suggestion == tmp_path / "y.v2.pdf"


def test_resolve_collision_overwrite_allows(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    out.write_bytes(b"%PDF")
    final = resolve_output_path(src, output=out, in_place=False, overwrite=True)
    assert final == out


def test_resolve_in_place_with_output_is_error(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    with pytest.raises(OutputResolutionError, match="mutually exclusive"):
        resolve_output_path(src, output=out, in_place=True, overwrite=False)


def test_resolve_no_output_no_in_place_is_error(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(OutputResolutionError, match="-o"):
        resolve_output_path(src, output=None, in_place=False, overwrite=False)


def test_resolve_input_equals_output_is_error(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(OutputResolutionError, match="--in-place"):
        resolve_output_path(src, output=src, in_place=False, overwrite=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_output_resolution.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the resolver and exceptions**

Append to `src/kuroi/core/output_resolution.py`:

```python
class OutputResolutionError(Exception):
    """User passed an invalid combination of `-o`/`--in-place`/`--overwrite`."""


class OutputCollisionError(Exception):
    """The output path exists and `--overwrite` was not passed."""

    def __init__(self, target: Path, suggestion: Path) -> None:
        super().__init__(
            f"output path {target} exists; pass --overwrite or use {suggestion}"
        )
        self.target = target
        self.suggestion = suggestion


def resolve_output_path(
    pdf: Path,
    *,
    output: Path | None,
    in_place: bool,
    overwrite: bool,
) -> Path:
    """Decide the final write path. Validates flag combinations and collisions."""
    if in_place and output is not None:
        raise OutputResolutionError("--in-place and -o are mutually exclusive")
    if in_place:
        return pdf
    if output is None:
        raise OutputResolutionError(
            "must pass either -o <path> or --in-place"
        )
    if output.resolve() == pdf.resolve():
        raise OutputResolutionError(
            "output path equals input; use --in-place to overwrite the input"
        )
    if output.exists() and not overwrite:
        raise OutputCollisionError(output, suggest_versioned_name(output))
    return output
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/test_output_resolution.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/output_resolution.py tests/core/test_output_resolution.py
git commit -m "feat(output): add resolve_output_path with collision and flag validation"
```

---

## Task 3: `output_lock` context manager

**Why this task exists:** Spec section 3.4 commits to per-output `O_CREAT|O_EXCL` lock files removed on clean exit and `SIGINT`/`SIGTERM`. This isolates concurrent runs against the same output.

**Files:**
- Create: `src/kuroi/core/locks.py`
- Create: `tests/core/test_locks.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_locks.py`:

```python
import os
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
    with pytest.raises(LockHeldError):
        with output_lock(target):
            pass
    # We did not remove the existing lock — only the holder removes it.
    assert lock_path.exists()


def test_output_lock_removes_on_exception(tmp_path: Path):
    target = tmp_path / "out.pdf"
    lock_path = target.with_suffix(target.suffix + ".kuroi.lock")
    with pytest.raises(RuntimeError):
        with output_lock(target):
            assert lock_path.exists()
            raise RuntimeError("simulated failure")
    assert not lock_path.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/core/test_locks.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `output_lock`**

Create `src/kuroi/core/locks.py`:

```python
"""Per-output advisory lock files for concurrent kuroi runs.

`output_lock(target)` creates `<target>.kuroi.lock` via `O_CREAT|O_EXCL`. If
the file exists, raises `LockHeldError` — the caller surfaces a clear message
instructing the user to delete the lock file if it's stale.

The lock is removed on clean exit AND on uncaught exceptions inside the
context. Stale locks (process killed before cleanup) are NOT auto-removed:
they are a real signal of a prior crash and the user clears them deliberately.
"""

from __future__ import annotations

import os
import signal
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path


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
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, lambda *a: (_cleanup(), prev_int(*a) if callable(prev_int) else None))
    signal.signal(signal.SIGTERM, lambda *a: (_cleanup(), prev_term(*a) if callable(prev_term) else None))

    try:
        yield
    finally:
        _cleanup()
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/test_locks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/locks.py tests/core/test_locks.py
git commit -m "feat(locks): add output_lock context manager with EXCL semantics"
```

---

## Task 4: Wire `--in-place`, `--overwrite`, and the lock into `run.py`

**Why this task exists:** This is the integration step. After this task `kuroi run` honors all three flags and the output lock.

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/cli/test_run.py`:

```python
def test_run_refuses_existing_output_without_overwrite(make_pdf, tmp_path, monkeypatch):
    pdf = make_pdf(["x"])
    out = tmp_path / "out.pdf"
    out.write_bytes(b"existing")

    result = runner.invoke(app, ["run", str(pdf), "-o", str(out)])

    assert result.exit_code == 2
    assert "out.v2.pdf" in result.stdout


def test_run_overwrite_replaces_existing(make_pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    # Stub the SDK as in earlier tests.
    from kuroi.providers import anthropic as ap

    class _Stub:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                r = type("R", (), {})()
                r.content = [type("B", (), {"text": '{"findings": []}'})()]
                r.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
                r.system_fingerprint = None
                return r

    real = ap.AnthropicProvider
    monkeypatch.setattr(
        ap, "AnthropicProvider",
        lambda **kw: real(client=_Stub(), model=kw.get("model", "claude-opus-4-7")),
    )

    pdf = make_pdf(["x"])
    out = tmp_path / "out.pdf"
    out.write_bytes(b"existing")

    result = runner.invoke(app, ["run", str(pdf), "-o", str(out), "-y", "--overwrite"])
    assert result.exit_code == 0
    # Output is a real PDF now, not the original 8 bytes.
    assert out.stat().st_size > 8


def test_run_in_place_writes_to_input_and_keeps_backup(make_pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    from kuroi.providers import anthropic as ap

    class _Stub:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                r = type("R", (), {})()
                r.content = [type("B", (), {"text": '{"findings": []}'})()]
                r.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
                r.system_fingerprint = None
                return r

    real = ap.AnthropicProvider
    monkeypatch.setattr(
        ap, "AnthropicProvider",
        lambda **kw: real(client=_Stub(), model=kw.get("model", "claude-opus-4-7")),
    )

    pdf = make_pdf(["x"])
    backup_dir = tmp_path / "backups"

    result = runner.invoke(app, [
        "run", str(pdf), "--in-place", "-y", "--backup-dir", str(backup_dir),
    ])
    assert result.exit_code == 0
    assert pdf.exists()
    # Backup was created.
    sessions = [p for p in backup_dir.iterdir() if p.is_dir()]
    assert len(sessions) == 1


def test_run_in_place_with_output_flag_is_usage_error(make_pdf, tmp_path):
    pdf = make_pdf(["x"])
    result = runner.invoke(app, [
        "run", str(pdf), "-o", str(tmp_path / "out.pdf"), "--in-place",
    ])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stdout


def test_run_held_lock_refuses(make_pdf, tmp_path):
    pdf = make_pdf(["x"])
    out = tmp_path / "out.pdf"
    lock_path = out.with_suffix(out.suffix + ".kuroi.lock")
    lock_path.write_text("")  # someone else's lock

    result = runner.invoke(app, ["run", str(pdf), "-o", str(out)])
    assert result.exit_code == 2
    assert "another kuroi run" in result.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cli/test_run.py -v -k "in_place or overwrite or held_lock"`
Expected: FAIL — flags don't exist.

- [ ] **Step 3: Update `cli/run.py` signature**

Make `output` optional and add the new flags:

```python
output: Path | None = typer.Option(None, "-o", "--output"),
in_place: bool = typer.Option(False, "--in-place", help="Write to the input path; backup is taken."),
overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing output file."),
```

- [ ] **Step 4: Use `resolve_output_path`**

Replace the existing `if output.resolve() == pdf.resolve(): ...` block with:

```python
from kuroi.core.output_resolution import (
    OutputCollisionError,
    OutputResolutionError,
    resolve_output_path,
)
from kuroi.core.locks import LockHeldError, output_lock

try:
    final_output = resolve_output_path(
        pdf, output=output, in_place=in_place, overwrite=overwrite
    )
except OutputResolutionError as exc:
    console.print(f"  [red]{exc}[/]")
    raise typer.Exit(code=2) from exc
except OutputCollisionError as exc:
    console.print(
        f"  [red]I won't overwrite {exc.target}.[/]\n\n"
        f"    Try one of:\n"
        f"      kuroi run {pdf} -o {exc.suggestion}\n"
        f"      kuroi run {pdf} -o {exc.target} --overwrite\n"
    )
    raise typer.Exit(code=2) from exc
```

Replace any remaining usage of the local `output` variable (where you write the redacted result) with `final_output`.

- [ ] **Step 5: Wrap the redaction-write in `output_lock`**

Wrap the temp-file → final-output region:

```python
try:
    with output_lock(final_output):
        # ... existing apply_redactions, verify, shutil.move, audit.close ...
        pass
except LockHeldError as exc:
    console.print(f"  [red]{exc}[/]")
    raise typer.Exit(code=2) from exc
```

(If you'd rather restructure the existing `try/except/finally` rather than nest, do that — the lock just needs to envelope the temp_out → output region.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "feat(run): add --in-place, --overwrite, and output lock"
```

---

## Self-review checklist

- [ ] `uv run pytest tests/ -q` — all green.
- [ ] `uv run mypy src/` — no errors.
- [ ] `uv run ruff check src/ tests/` — no warnings.
- [ ] `kuroi run sample.pdf -o existing.pdf` — exit 2, suggests `existing.v2.pdf`.
- [ ] `kuroi run sample.pdf -o existing.pdf --overwrite` — succeeds.
- [ ] `kuroi run sample.pdf --in-place -y` — writes to `sample.pdf`, backup present in backup directory.
- [ ] `kuroi run sample.pdf --in-place -o other.pdf` — exit 2, "mutually exclusive".
- [ ] After a successful run, no `*.kuroi.lock` files remain.
- [ ] If a run is interrupted with `kill -INT`, the `.kuroi.lock` is cleaned up before exit (signal handler ran).
