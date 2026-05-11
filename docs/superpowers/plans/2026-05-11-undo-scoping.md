# Scoped `kuroi undo` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `kuroi undo` so a user can target a specific file, a page range, or a specific element (interactively or via flags), all via the same regenerate-from-backup mechanism.

**Architecture:** Audit log + backup PDF are loaded for the requested session. An "exclusion set" of finding indices is built from CLI filters and an interactive picker. `apply_redactions(backup, kept_findings, ...)` rewrites the output PDF; the result is verified and atomically moved into place. A separate `<undo_ts>.undo.jsonl` records the operation.

**Tech Stack:** Python 3, `typer`, `rich`, `pymupdf`, new dep `questionary>=2.0`. Existing helpers: `kuroi.core.audit`, `kuroi.core.backup`, `kuroi.core.redaction`, `kuroi.core.verification`, `kuroi.core.pdf`, `kuroi.core.locks`, `kuroi.core.findings`, `kuroi.core.page_selection`, `kuroi.core.config`.

**Spec:** `docs/superpowers/specs/2026-05-11-undo-scoping-design.md`

---

## File map

**New files:**
- `src/kuroi/core/audit_replay.py` — Replayable* dataclasses, `load_session`, `build_exclusion_set`.
- `src/kuroi/core/undo_audit.py` — `UndoAuditLog` class mirroring `kuroi.core.audit.AuditLog`.
- `src/kuroi/cli/undo_picker.py` — interactive picker wrapper around `questionary`.
- `tests/core/test_audit_replay.py`
- `tests/core/test_undo_audit.py`
- `tests/cli/test_undo_picker.py`

**Modified files:**
- `pyproject.toml` — add `questionary>=2.0` to `dependencies`.
- `src/kuroi/core/backup.py` — add `find_session_by_path` and `find_session_by_timestamp` helpers.
- `src/kuroi/cli/undo.py` — near-total rewrite (current ~63 lines → ~250 lines).
- `tests/cli/test_undo.py` — extend with new flag/picker matrix.

---

## Task 1: Audit-log parsing (`load_session`)

**Files:**
- Create: `src/kuroi/core/audit_replay.py`
- Create: `tests/core/test_audit_replay.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_audit_replay.py`:

```python
"""Tests for audit-log replay parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kuroi.core.audit_replay import (
    ReplayableFinding,
    ReplayableSession,
    load_session,
)


def _write_log(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_load_session_parses_session_start_and_applied_findings(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "session_start",
                "session_id": "abc-123",
                "input_path": "/work/in.pdf",
                "output_path": "/work/out.pdf",
            },
            {
                "event": "finding",
                "page": 3,
                "word_start": 12,
                "word_end": 14,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": [10.0, 20.0, 90.0, 30.0],
                "decision": "applied",
            },
            {
                "event": "finding",
                "page": 5,
                "word_start": 2,
                "word_end": 2,
                "kind": "person",
                "confidence": "medium",
                "source": "rules:pii-en",
                "bbox": None,
                "decision": "applied",
            },
            {"event": "session_end", "status": "ok"},
        ],
    )

    session = load_session(log)

    assert isinstance(session, ReplayableSession)
    assert session.session_id == "abc-123"
    assert session.input_path == Path("/work/in.pdf")
    assert session.output_path == Path("/work/out.pdf")
    assert len(session.findings) == 2
    assert session.findings[0] == ReplayableFinding(
        page=3,
        word_start=12,
        word_end=14,
        kind="email",
        confidence="high",
        source="llm",
        bbox=(10.0, 20.0, 90.0, 30.0),
    )
    assert session.findings[1].bbox is None


def test_load_session_skips_non_applied_decisions(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "session_start",
                "session_id": "x",
                "input_path": "/i",
                "output_path": "/o",
            },
            {
                "event": "finding",
                "page": 1,
                "word_start": 0,
                "word_end": 0,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": None,
                "decision": "applied",
            },
            {
                "event": "finding",
                "page": 1,
                "word_start": 5,
                "word_end": 5,
                "kind": "phone",
                "confidence": "low",
                "source": "llm",
                "bbox": None,
                "decision": "rejected",
            },
        ],
    )

    session = load_session(log)
    assert len(session.findings) == 1
    assert session.findings[0].kind == "email"


def test_load_session_tolerates_unknown_fields(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "session_start",
                "session_id": "x",
                "input_path": "/i",
                "output_path": "/o",
                "future_field": 42,
            },
            {
                "event": "finding",
                "page": 1,
                "word_start": 0,
                "word_end": 0,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": None,
                "decision": "applied",
                "unexpected": "ignore me",
            },
            {"event": "some_future_event", "payload": True},
        ],
    )

    session = load_session(log)
    assert session.session_id == "x"
    assert len(session.findings) == 1


def test_load_session_missing_session_start_raises(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "finding",
                "page": 1,
                "word_start": 0,
                "word_end": 0,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": None,
                "decision": "applied",
            }
        ],
    )

    with pytest.raises(ValueError, match="session_start"):
        load_session(log)


def test_load_session_malformed_line_raises(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    log.write_text('{"event": "session_start", "session_id": "x", "input_path": "/i", "output_path": "/o"}\n{not json\n')
    with pytest.raises(ValueError, match="line 2"):
        load_session(log)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_audit_replay.py -v`
Expected: collection error (`ModuleNotFoundError: kuroi.core.audit_replay`).

- [ ] **Step 3: Write the implementation**

Create `src/kuroi/core/audit_replay.py`:

```python
"""Replay an existing run's audit log for `kuroi undo`.

Reads the NDJSON audit log written by `AuditLog.open` and reconstructs the
set of `applied` findings. The output is consumed by the exclusion-set
builder and the regeneration step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ReplayableFinding:
    page: int
    word_start: int
    word_end: int
    kind: str
    confidence: Confidence
    source: str
    bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class ReplayableSession:
    session_id: str
    input_path: Path
    output_path: Path
    findings: tuple[ReplayableFinding, ...]


def load_session(audit_path: Path) -> ReplayableSession:
    """Parse `audit_path` (NDJSON) into a ReplayableSession.

    Only `finding` events with `decision == "applied"` are included; all other
    events are skipped. Unknown fields are tolerated for forward compat.
    Raises `ValueError` on missing `session_start` or a malformed JSON line.
    """
    session_start: dict | None = None
    findings: list[ReplayableFinding] = []

    with audit_path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{audit_path}: line {lineno}: malformed JSON: {exc}") from exc

            event = obj.get("event")
            if event == "session_start":
                session_start = obj
            elif event == "finding" and obj.get("decision") == "applied":
                bbox_raw = obj.get("bbox")
                bbox = tuple(bbox_raw) if bbox_raw is not None else None
                findings.append(
                    ReplayableFinding(
                        page=int(obj["page"]),
                        word_start=int(obj["word_start"]),
                        word_end=int(obj["word_end"]),
                        kind=str(obj["kind"]),
                        confidence=obj["confidence"],
                        source=str(obj["source"]),
                        bbox=bbox,  # type: ignore[arg-type]
                    )
                )

    if session_start is None:
        raise ValueError(f"{audit_path}: no session_start event found")

    return ReplayableSession(
        session_id=str(session_start["session_id"]),
        input_path=Path(session_start["input_path"]),
        output_path=Path(session_start["output_path"]),
        findings=tuple(findings),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_audit_replay.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/audit_replay.py tests/core/test_audit_replay.py
git commit -m "feat(undo): add audit-log replay parser

Read the NDJSON audit log produced by kuroi run and reconstruct the
applied findings as ReplayableSession. Foundation for scoped undo.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Exclusion-set builder

**Files:**
- Modify: `src/kuroi/core/audit_replay.py`
- Modify: `tests/core/test_audit_replay.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_audit_replay.py`:

```python
from kuroi.core.audit_replay import build_exclusion_set


def _make_findings() -> tuple[ReplayableFinding, ...]:
    return (
        ReplayableFinding(3, 12, 14, "email", "high", "llm", None),       # idx 0
        ReplayableFinding(3, 20, 21, "person", "medium", "rules", None),  # idx 1
        ReplayableFinding(5, 0, 2, "person", "high", "llm", None),        # idx 2
        ReplayableFinding(5, 8, 8, "date", "low", "llm", None),           # idx 3
        ReplayableFinding(7, 4, 6, "email", "high", "llm", None),         # idx 4
    )


def test_build_exclusion_set_by_pages() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=(3, 5),
        page=None,
        kind=None,
        words=None,
        picker_indices=(),
    )
    assert result == frozenset({0, 1, 2, 3})


def test_build_exclusion_set_by_page_and_kind() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=3,
        kind="email",
        words=None,
        picker_indices=(),
    )
    assert result == frozenset({0})


def test_build_exclusion_set_by_page_and_words_exact_range() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=5,
        kind=None,
        words=(0, 2),
        picker_indices=(),
    )
    assert result == frozenset({2})


def test_build_exclusion_set_empty_when_filters_match_nothing() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=3,
        kind="iban",
        words=None,
        picker_indices=(),
    )
    assert result == frozenset()


def test_build_exclusion_set_picker_indices_union() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=3,
        kind="email",
        words=None,
        picker_indices=(2, 4),
    )
    assert result == frozenset({0, 2, 4})


def test_build_exclusion_set_no_filters_returns_empty() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=None,
        kind=None,
        words=None,
        picker_indices=(),
    )
    assert result == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_audit_replay.py -v -k build_exclusion`
Expected: `ImportError: cannot import name 'build_exclusion_set'`.

- [ ] **Step 3: Add the implementation**

Append to `src/kuroi/core/audit_replay.py`:

```python
def build_exclusion_set(
    findings: tuple[ReplayableFinding, ...],
    *,
    pages: tuple[int, ...] | None,
    page: int | None,
    kind: str | None,
    words: tuple[int, int] | None,
    picker_indices: tuple[int, ...],
) -> frozenset[int]:
    """Return finding indices to exclude (i.e., un-redact) based on filters.

    --pages, --page, --kind, --words are AND-composed inside the flag-derived
    set. Picker indices are union'd on top. If no filters or picker indices
    are given, returns an empty set — the CLI layer turns that into exit 6
    (or the no-selector full-restore path, depending on context).
    """
    flag_filters_present = (
        pages is not None or page is not None or kind is not None or words is not None
    )

    flag_set: set[int] = set()
    if flag_filters_present:
        for idx, f in enumerate(findings):
            if pages is not None and f.page not in pages:
                continue
            if page is not None and f.page != page:
                continue
            if kind is not None and f.kind != kind:
                continue
            if words is not None and (f.word_start, f.word_end) != words:
                continue
            flag_set.add(idx)

    return frozenset(flag_set | set(picker_indices))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_audit_replay.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/audit_replay.py tests/core/test_audit_replay.py
git commit -m "feat(undo): add exclusion-set builder for replay findings

AND-compose --pages, --page, --kind, --words; union picker indices on top.
Pure function over ReplayableFinding indices.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Undo audit log writer (`UndoAuditLog`)

**Files:**
- Create: `src/kuroi/core/undo_audit.py`
- Create: `tests/core/test_undo_audit.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_undo_audit.py`:

```python
"""Tests for the undo audit log."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from kuroi.core.audit_replay import ReplayableFinding
from kuroi.core.undo_audit import UndoAuditLog


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_undo_audit_open_writes_undo_start(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit" / "2026-05-11T15-08-44Z-xyz.undo.jsonl"
    log = UndoAuditLog.open(
        audit_path,
        source_session_id="abc-123",
        source_session_ts="2026-05-11T14-22-13Z-abc",
        source_audit_path=Path("/.../source.jsonl"),
        input_path=Path("/work/in.pdf"),
        output_path=Path("/work/out.pdf"),
        backup_path=Path("/.../backup.pdf"),
        selector={"interactive": True, "pages": None, "page": None, "kind": None, "words": None},
        findings_total=8,
        findings_excluded=2,
    )
    log.close(
        status="ok",
        redactions_kept=6,
        redactions_un_redacted=2,
        verify_result="pass",
        verify_leak_count=0,
        output_sha256="deadbeef",
    )

    lines = _read_lines(audit_path)
    assert lines[0]["event"] == "undo_start"
    assert lines[0]["schema_kind"] == "undo"
    assert lines[0]["source_session_id"] == "abc-123"
    assert lines[0]["findings_total"] == 8
    assert lines[0]["findings_excluded"] == 2
    assert lines[-1]["event"] == "undo_end"
    assert lines[-1]["status"] == "ok"
    assert lines[-1]["output_sha256"] == "deadbeef"


def test_undo_audit_write_undo_finding(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit" / "u.undo.jsonl"
    log = UndoAuditLog.open(
        audit_path,
        source_session_id="x",
        source_session_ts="ts",
        source_audit_path=Path("/s"),
        input_path=Path("/i"),
        output_path=Path("/o"),
        backup_path=Path("/b"),
        selector={"interactive": False, "pages": "3-5", "page": None, "kind": None, "words": None},
        findings_total=3,
        findings_excluded=1,
    )
    log.write_undo_finding(
        ReplayableFinding(3, 12, 14, "email", "high", "llm", None),
        text_sha256="cafef00d",
        context_sha256="b0b1b2b3",
    )
    log.close(
        status="ok",
        redactions_kept=2,
        redactions_un_redacted=1,
        verify_result="pass",
        verify_leak_count=0,
        output_sha256="abc",
    )

    lines = _read_lines(audit_path)
    finding_line = [ln for ln in lines if ln["event"] == "undo_finding"][0]
    assert finding_line["page"] == 3
    assert finding_line["word_start"] == 12
    assert finding_line["word_end"] == 14
    assert finding_line["kind"] == "email"
    assert finding_line["text_sha256"] == "cafef00d"
    assert finding_line["context_sha256"] == "b0b1b2b3"


def test_undo_audit_uses_restrictive_permissions(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit" / "u.undo.jsonl"
    log = UndoAuditLog.open(
        audit_path,
        source_session_id="x",
        source_session_ts="ts",
        source_audit_path=Path("/s"),
        input_path=Path("/i"),
        output_path=Path("/o"),
        backup_path=Path("/b"),
        selector={"interactive": False, "pages": None, "page": None, "kind": None, "words": None},
        findings_total=0,
        findings_excluded=0,
    )
    log.close(
        status="ok",
        redactions_kept=0,
        redactions_un_redacted=0,
        verify_result="pass",
        verify_leak_count=0,
        output_sha256="",
    )

    file_mode = stat.S_IMODE(os.stat(audit_path).st_mode)
    dir_mode = stat.S_IMODE(os.stat(audit_path.parent).st_mode)
    assert file_mode == 0o600
    assert dir_mode == 0o700


def test_undo_audit_close_on_exception_emits_failed_end(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit" / "u.undo.jsonl"
    log = UndoAuditLog.open(
        audit_path,
        source_session_id="x",
        source_session_ts="ts",
        source_audit_path=Path("/s"),
        input_path=Path("/i"),
        output_path=Path("/o"),
        backup_path=Path("/b"),
        selector={"interactive": False, "pages": None, "page": None, "kind": None, "words": None},
        findings_total=1,
        findings_excluded=1,
    )
    log.close(
        status="failed",
        redactions_kept=0,
        redactions_un_redacted=0,
        verify_result="skipped",
        verify_leak_count=0,
        output_sha256="",
    )

    lines = _read_lines(audit_path)
    assert lines[-1]["status"] == "failed"
    assert lines[-1]["verify_result"] == "skipped"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_undo_audit.py -v`
Expected: collection error (`ModuleNotFoundError: kuroi.core.undo_audit`).

- [ ] **Step 3: Write the implementation**

Create `src/kuroi/core/undo_audit.py`:

```python
"""Append-only NDJSON audit log for `kuroi undo` operations.

Mirrors `kuroi.core.audit.AuditLog` in structure and hardening (0700 parent,
0600 file). The original session's audit log is never reopened — the undo
log references it by path and session_id for chain-of-custody.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal

from kuroi.core.audit_replay import ReplayableFinding

UndoStatus = Literal["ok", "verify_failed", "failed"]
VerifyResult = Literal["pass", "fail", "skipped"]


class UndoAuditLog:
    def __init__(self, fh: IO[str]) -> None:
        self._fh = fh
        self._started = time.monotonic()

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        source_session_id: str,
        source_session_ts: str,
        source_audit_path: Path,
        input_path: Path,
        output_path: Path,
        backup_path: Path,
        selector: dict[str, Any],
        findings_total: int,
        findings_excluded: int,
    ) -> "UndoAuditLog":
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(str(path.parent), 0o700)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        fh = os.fdopen(fd, "w", encoding="utf-8")
        log = cls(fh)
        payload: dict[str, Any] = {
            "event": "undo_start",
            "audit_schema_version": 1,
            "schema_kind": "undo",
            "undo_id": str(uuid.uuid4()),
            "ts_start": _now_iso(),
            "kuroi_version": _kuroi_version(),
            "source_session_id": source_session_id,
            "source_session_ts": source_session_ts,
            "source_audit_path": str(source_audit_path),
            "input_path": str(input_path),
            "output_path": str(output_path),
            "backup_path": str(backup_path),
            "selector": selector,
            "findings_total": findings_total,
            "findings_excluded": findings_excluded,
        }
        log._write(payload)
        return log

    def write_undo_finding(
        self,
        finding: ReplayableFinding,
        *,
        text_sha256: str,
        context_sha256: str,
    ) -> None:
        self._write(
            {
                "event": "undo_finding",
                "ts": _now_iso(),
                "page": finding.page,
                "word_start": finding.word_start,
                "word_end": finding.word_end,
                "kind": finding.kind,
                "source": finding.source,
                "text_sha256": text_sha256,
                "context_sha256": context_sha256,
            }
        )

    def close(
        self,
        *,
        status: UndoStatus,
        redactions_kept: int,
        redactions_un_redacted: int,
        verify_result: VerifyResult,
        verify_leak_count: int,
        output_sha256: str,
    ) -> None:
        duration_ms = int((time.monotonic() - self._started) * 1000)
        self._write(
            {
                "event": "undo_end",
                "ts_end": _now_iso(),
                "status": status,
                "duration_ms": duration_ms,
                "redactions_kept": redactions_kept,
                "redactions_un_redacted": redactions_un_redacted,
                "verify_result": verify_result,
                "verify_leak_count": verify_leak_count,
                "output_sha256": output_sha256,
            }
        )
        self._fh.close()

    def _write(self, payload: dict[str, Any]) -> None:
        self._fh.write(json.dumps(payload, separators=(",", ":")))
        self._fh.write("\n")
        self._fh.flush()


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kuroi_version() -> str:
    from kuroi import __version__

    return __version__
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_undo_audit.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/undo_audit.py tests/core/test_undo_audit.py
git commit -m "feat(undo): add UndoAuditLog for undo operation chain-of-custody

NDJSON log written to <undo_ts>.undo.jsonl, mode 0600 / parent 0700.
References the source session by id + path; never reopens the source log.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add `questionary` dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`. Locate the `dependencies = [...]` array and add the new entry next to the existing ones:

```toml
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "pymupdf>=1.24",
    "questionary>=2.0",
]
```

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv lock`
Expected: `uv.lock` is updated; no errors.

- [ ] **Step 3: Sync the environment**

Run: `uv sync`
Expected: questionary and its transitive deps (prompt_toolkit, wcwidth) are installed.

- [ ] **Step 4: Verify the import works**

Run: `uv run python -c "import questionary; print(questionary.__version__)"`
Expected: a version string `>=2.0` printed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add questionary for the kuroi undo picker

Stock multi-select prompt for selecting findings to un-redact. Built on
prompt_toolkit; ~3 transitive deps.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Interactive picker wrapper

**Files:**
- Create: `src/kuroi/cli/undo_picker.py`
- Create: `tests/cli/test_undo_picker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cli/test_undo_picker.py`:

```python
"""Tests for the undo interactive picker wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kuroi.cli.undo_picker import (
    _build_choices,
    _expand_page_sentinels,
    PageSentinel,
    pick_findings,
)
from kuroi.core.audit_replay import ReplayableFinding, ReplayableSession


def _session() -> ReplayableSession:
    return ReplayableSession(
        session_id="x",
        input_path=Path("/i"),
        output_path=Path("/o"),
        findings=(
            ReplayableFinding(3, 12, 14, "email", "high", "llm", None),     # 0
            ReplayableFinding(3, 20, 21, "person", "medium", "rules", None),# 1
            ReplayableFinding(5, 0, 2, "person", "high", "llm", None),      # 2
            ReplayableFinding(7, 4, 6, "email", "high", "llm", None),       # 3
        ),
    )


def test_build_choices_groups_by_page_with_headers() -> None:
    session = _session()
    text_by_index = {0: "alice@example.com", 1: "Alice", 2: "Bob", 3: "carol@example.com"}
    choices = _build_choices(session, text_by_index=text_by_index, pages_filter=None)

    # 3 pages → 3 page-header rows + 4 finding rows
    assert len(choices) == 7
    titles = [c.title for c in choices]
    assert "Page 3" in titles[0]
    assert "Page 5" in [t for t in titles if "Page 5" in t][0]


def test_build_choices_pages_filter_drops_other_pages() -> None:
    session = _session()
    text_by_index = {0: "a", 1: "b", 2: "c", 3: "d"}
    choices = _build_choices(session, text_by_index=text_by_index, pages_filter=(3,))
    # only page 3 group: 1 header + 2 findings
    assert len(choices) == 3


def test_expand_page_sentinels_expands_to_all_indices_on_page() -> None:
    session = _session()
    selected: list[Any] = [PageSentinel(3), 2]
    result = _expand_page_sentinels(selected, session)
    assert result == (0, 1, 2)  # page 3 → indices 0,1; explicit 2


def test_expand_page_sentinels_dedupes() -> None:
    session = _session()
    selected: list[Any] = [PageSentinel(3), 0, 1]
    result = _expand_page_sentinels(selected, session)
    assert result == (0, 1)


def test_pick_findings_returns_empty_on_no_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    text_by_index = {i: "x" for i in range(4)}

    class FakeAsk:
        def ask(self) -> list:
            return []

    monkeypatch.setattr(
        "kuroi.cli.undo_picker.questionary.checkbox",
        lambda *args, **kwargs: FakeAsk(),
    )

    result = pick_findings(session, text_by_index=text_by_index, pages_filter=None)
    assert result == ()


def test_pick_findings_keyboardinterrupt_on_none(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    text_by_index = {i: "x" for i in range(4)}

    class FakeAsk:
        def ask(self) -> None:
            return None  # questionary returns None on Ctrl-C

    monkeypatch.setattr(
        "kuroi.cli.undo_picker.questionary.checkbox",
        lambda *args, **kwargs: FakeAsk(),
    )

    with pytest.raises(KeyboardInterrupt):
        pick_findings(session, text_by_index=text_by_index, pages_filter=None)


def test_pick_findings_returns_selected_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    text_by_index = {i: "x" for i in range(4)}

    class FakeAsk:
        def ask(self) -> list:
            return [0, 3]  # pick first and last findings

    monkeypatch.setattr(
        "kuroi.cli.undo_picker.questionary.checkbox",
        lambda *args, **kwargs: FakeAsk(),
    )

    result = pick_findings(session, text_by_index=text_by_index, pages_filter=None)
    assert result == (0, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_undo_picker.py -v`
Expected: collection error (`ModuleNotFoundError: kuroi.cli.undo_picker`).

- [ ] **Step 3: Write the implementation**

Create `src/kuroi/cli/undo_picker.py`:

```python
"""Interactive picker for `kuroi undo`.

Wraps `questionary.checkbox` with page-header group rows. Page headers are
encoded as `PageSentinel(page_number)` choice values; selecting a header
expands to every finding index on that page when results are unpacked.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any

import questionary

from kuroi.core.audit_replay import ReplayableSession


@dataclass(frozen=True)
class PageSentinel:
    page: int


def _truncate(text: str, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    if max_width <= 1:
        return "…"
    return text[: max_width - 1] + "…"


def _build_choices(
    session: ReplayableSession,
    *,
    text_by_index: dict[int, str],
    pages_filter: tuple[int, ...] | None,
) -> list[questionary.Choice]:
    indices_by_page: dict[int, list[int]] = {}
    for idx, f in enumerate(session.findings):
        if pages_filter is not None and f.page not in pages_filter:
            continue
        indices_by_page.setdefault(f.page, []).append(idx)

    term_width = shutil.get_terminal_size((80, 24)).columns
    text_width = max(20, term_width - 40)

    choices: list[questionary.Choice] = []
    for page in sorted(indices_by_page):
        count = len(indices_by_page[page])
        choices.append(
            questionary.Choice(
                title=f"Page {page}   ({count} finding{'s' if count != 1 else ''})",
                value=PageSentinel(page),
            )
        )
        for idx in indices_by_page[page]:
            f = session.findings[idx]
            text = _truncate(text_by_index.get(idx, ""), text_width)
            choices.append(
                questionary.Choice(
                    title=f"    {f.kind:<10}  {f.source:<10}  {text!r}",
                    value=idx,
                )
            )
    return choices


def _expand_page_sentinels(
    selected: list[Any],
    session: ReplayableSession,
) -> tuple[int, ...]:
    expanded: set[int] = set()
    page_pages = {s.page for s in selected if isinstance(s, PageSentinel)}
    for s in selected:
        if isinstance(s, PageSentinel):
            for idx, f in enumerate(session.findings):
                if f.page == s.page:
                    expanded.add(idx)
        elif isinstance(s, int):
            expanded.add(s)
    return tuple(sorted(expanded))


def pick_findings(
    session: ReplayableSession,
    *,
    text_by_index: dict[int, str],
    pages_filter: tuple[int, ...] | None,
) -> tuple[int, ...]:
    """Open the picker and return finding indices the user selected.

    Returns an empty tuple on confirmed empty selection. Raises
    `KeyboardInterrupt` if the user cancels (Ctrl-C).
    """
    choices = _build_choices(
        session, text_by_index=text_by_index, pages_filter=pages_filter
    )
    header = (
        f"Session {session.session_id} — {session.input_path.name}\n"
        "Use ↑/↓ to move, Space to toggle, Enter to confirm, Ctrl-C to cancel."
    )
    answer = questionary.checkbox(header, choices=choices).ask()
    if answer is None:
        raise KeyboardInterrupt
    return _expand_page_sentinels(answer, session)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cli/test_undo_picker.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/undo_picker.py tests/cli/test_undo_picker.py
git commit -m "feat(undo): add interactive picker wrapper around questionary

PageSentinel rows group findings by page; selecting a header expands to
every finding on that page. Returns selected indices for the exclusion set.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Session locator helpers in `backup.py`

**Files:**
- Modify: `src/kuroi/core/backup.py`
- Modify: `tests/core/test_backup.py` (may not exist; create if absent)

- [ ] **Step 1: Check if `tests/core/test_backup.py` exists**

Run: `ls tests/core/test_backup.py 2>/dev/null && echo EXISTS || echo MISSING`

If MISSING, create the file with this minimal content:

```python
"""Tests for kuroi.core.backup helpers."""

from __future__ import annotations

from pathlib import Path

from kuroi.core.backup import (
    create_backup,
    find_session_by_path,
    find_session_by_timestamp,
)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/core/test_backup.py`:

```python
from collections.abc import Callable


def test_find_session_by_path_returns_newest_match(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["hello"], filename="doc.pdf")
    backup_root = tmp_path / "backups"

    older = create_backup(pdf, backup_root=backup_root)
    newer = create_backup(pdf, backup_root=backup_root)
    assert older.timestamp < newer.timestamp

    result = find_session_by_path(backup_root, pdf)
    assert result is not None
    assert result.timestamp == newer.timestamp


def test_find_session_by_path_returns_none_when_no_match(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["hello"], filename="doc.pdf")
    other = make_pdf(["world"], filename="other.pdf")
    backup_root = tmp_path / "backups"
    create_backup(pdf, backup_root=backup_root)

    assert find_session_by_path(backup_root, other) is None


def test_find_session_by_path_resolves_symlinks_consistently(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["hello"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(pdf, backup_root=backup_root)

    link = tmp_path / "alias.pdf"
    link.symlink_to(pdf)

    result = find_session_by_path(backup_root, link)
    assert result is not None
    assert result.timestamp


def test_find_session_by_timestamp_returns_match(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["hello"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    created = create_backup(pdf, backup_root=backup_root)

    result = find_session_by_timestamp(backup_root, created.timestamp)
    assert result is not None
    assert result.original_path == pdf


def test_find_session_by_timestamp_missing_returns_none(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    assert find_session_by_timestamp(backup_root, "2026-01-01T00-00-00Z-xxxxxx") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/core/test_backup.py -v`
Expected: ImportError on `find_session_by_path` / `find_session_by_timestamp`.

- [ ] **Step 4: Add the implementation**

Append to `src/kuroi/core/backup.py` (after `latest_backup`):

```python
def find_session_by_path(backup_root: Path, target: Path) -> Backup | None:
    """Return the most recent backup whose original_path matches `target`.

    Path matching uses `Path.resolve(strict=False)` on both sides so symlinks
    and relative-vs-absolute spellings reconcile.
    """
    if not backup_root.is_dir():
        return None
    target_resolved = target.resolve(strict=False)
    candidates: list[tuple[str, Backup]] = []
    for session_dir in backup_root.iterdir():
        if not session_dir.is_dir():
            continue
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            continue
        orig = Path(data["original_path"]).resolve(strict=False)
        if orig == target_resolved:
            candidates.append(
                (
                    session_dir.name,
                    Backup(
                        timestamp=data["timestamp"],
                        copy_path=Path(data["copy_path"]),
                        manifest_path=manifest_path,
                        original_path=Path(data["original_path"]),
                    ),
                )
            )
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def find_session_by_timestamp(backup_root: Path, ts: str) -> Backup | None:
    """Return the backup whose session directory matches `ts` exactly."""
    session_dir = backup_root / ts
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return None
    return Backup(
        timestamp=data["timestamp"],
        copy_path=Path(data["copy_path"]),
        manifest_path=manifest_path,
        original_path=Path(data["original_path"]),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/core/test_backup.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/core/backup.py tests/core/test_backup.py
git commit -m "feat(backup): add find_session_by_path and _by_timestamp locators

Lookup helpers used by kuroi undo to map INPUT/--session to a Backup.
Path matching is symlink-tolerant via Path.resolve(strict=False).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: CLI scaffolding — new flags, conflict checks, back-compat preserved

**Files:**
- Modify: `src/kuroi/cli/undo.py`
- Modify: `tests/cli/test_undo.py`

This task widens the `undo` command's signature without changing behavior. All existing tests must continue to pass. The new flags are accepted but ignored beyond validation; subsequent tasks wire them in.

- [ ] **Step 1: Add `import pytest` to `tests/cli/test_undo.py`**

The existing test file doesn't import pytest; later tasks (9–11) use
`pytest.MonkeyPatch`. Add `import pytest` to the top of the file, next to
the other imports:

```python
import pytest
```

- [ ] **Step 2: Write a failing test for the new flag surface**

Append to `tests/cli/test_undo.py`:

```python
def test_undo_accepts_new_flags_without_breaking_back_compat(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    original = make_pdf(["original body"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(original, backup_root=backup_root)
    original.write_bytes(b"%PDF-1.4\n% mutated\n")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(original),
            "-y",
            "--backup-dir",
            str(backup_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert original.read_bytes().startswith(b"%PDF")


def test_undo_rejects_conflicting_words_without_page(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    original = make_pdf(["x"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(original, backup_root=backup_root)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(original),
            "-y",
            "--words",
            "1-3",
            "--backup-dir",
            str(backup_root),
        ],
    )
    assert result.exit_code == 2
    assert "--words requires --page" in result.stdout
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/cli/test_undo.py -v -k "new_flags or rejects_conflicting"`
Expected: failures (`--words` and positional `INPUT` aren't recognized yet).

- [ ] **Step 4: Add the new flag surface to `undo.py`**

Replace the contents of `src/kuroi/cli/undo.py` with:

```python
"""kuroi undo — restore from backup, optionally scoped to file/pages/elements."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.backup import latest_backup, sweep_backups
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
    xdg_data_home,
)

console = Console()


def _parse_words(spec: str | None) -> tuple[int, int] | None:
    if spec is None:
        return None
    try:
        left, _, right = spec.partition("-")
        return (int(left), int(right))
    except (ValueError, AttributeError) as exc:
        raise typer.BadParameter(f"--words must be START-END (got {spec!r})") from exc


def undo(
    input: Path | None = typer.Argument(
        None,
        help="PDF whose backup should be restored. Default: most recent backup overall.",
    ),
    yes: bool = typer.Option(False, "-y", help="Skip the restore confirmation."),
    backup_dir: Path | None = typer.Option(
        None,
        "--backup-dir",
        help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups].",
    ),
    audit_dir: Path | None = typer.Option(
        None,
        "--audit-dir",
        help="Audit directory [default: $XDG_DATA_HOME/kuroi/audit].",
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Exact backup-session timestamp to undo (e.g. 2026-05-11T14-22-13Z-abc123).",
    ),
    pages: str | None = typer.Option(
        None,
        "--pages",
        help="Restrict undo to a page range (same syntax as `kuroi run --pages`).",
    ),
    page: int | None = typer.Option(None, "--page", help="Single page filter."),
    kind: str | None = typer.Option(None, "--kind", help="Finding kind filter (e.g. email)."),
    words: str | None = typer.Option(
        None,
        "--words",
        help="Word range filter on the form START-END (requires --page).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan, write nothing."),
) -> None:
    """Restore an original PDF from a backup; supports file/page/element scoping."""
    # Argument cross-checks (subsequent tasks add behavioral wiring).
    if words is not None and page is None:
        raise typer.BadParameter("--words requires --page")
    _ = _parse_words(words)  # validate format even if not yet used

    if backup_dir is None:
        backup_dir = xdg_data_home() / "kuroi" / "backups"
    if audit_dir is None:
        audit_dir = xdg_data_home() / "kuroi" / "audit"

    try:
        config = resolve_config(
            ConfigOverrides(),
            env=os.environ,
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)

    # TODO(Task 8): replace this block with the locator + audit-driven flow.
    bak = latest_backup(backup_dir)
    if bak is None:
        console.print(f"  No backup found in {backup_dir}.")
        raise typer.Exit(code=1)

    console.print(f"  Last backup: {bak.timestamp}")
    console.print(f"  Will restore: {bak.original_path}")

    if not yes:
        confirm = typer.confirm("Restore now?", default=True)
        if not confirm:
            raise typer.Exit(code=0)

    bak.original_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak.copy_path, bak.original_path)
    console.print("  Restored.")
```

- [ ] **Step 5: Run the full undo test file to confirm nothing regressed**

Run: `pytest tests/cli/test_undo.py -v`
Expected: all tests pass (existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/kuroi/cli/undo.py tests/cli/test_undo.py
git commit -m "feat(undo): scaffold new CLI flags (INPUT, --session, --pages, --page, --kind, --words, --dry-run)

Flags are accepted and validated but not yet wired to behavior. Existing
no-arg behavior preserved. --words requires --page; format checked.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Session locator integration

**Files:**
- Modify: `src/kuroi/cli/undo.py`
- Modify: `tests/cli/test_undo.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_undo.py`:

```python
def test_undo_with_input_path_picks_matching_backup(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    a = make_pdf(["AAA"], filename="a.pdf")
    b = make_pdf(["BBB"], filename="b.pdf")
    backup_root = tmp_path / "backups"
    create_backup(a, backup_root=backup_root)
    create_backup(b, backup_root=backup_root)  # most recent overall
    a.write_bytes(b"%PDF-1.4\n% mutated A\n")
    b.write_bytes(b"%PDF-1.4\n% mutated B\n")

    runner = CliRunner()
    # Without INPUT, current behavior restores the latest overall (b).
    # With INPUT=a, must restore a even though b is newer.
    result = runner.invoke(
        app,
        ["undo", str(a), "-y", "--backup-dir", str(backup_root)],
    )
    assert result.exit_code == 0, result.stdout
    assert a.read_bytes().startswith(b"%PDF")
    # b is left as we mutated it
    assert b.read_bytes() == b"%PDF-1.4\n% mutated B\n"


def test_undo_with_input_path_no_match_exits_1(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    a = make_pdf(["AAA"], filename="a.pdf")
    backup_root = tmp_path / "backups"
    create_backup(a, backup_root=backup_root)
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(b"%PDF-1.4\n")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["undo", str(elsewhere), "-y", "--backup-dir", str(backup_root)],
    )
    assert result.exit_code == 1
    assert "no backup" in result.stdout.lower()
    assert "elsewhere.pdf" in result.stdout


def test_undo_with_session_flag_picks_exact_session(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    a = make_pdf(["AAA"], filename="a.pdf")
    backup_root = tmp_path / "backups"
    first = create_backup(a, backup_root=backup_root)
    create_backup(a, backup_root=backup_root)  # newer, but not what we want
    a.write_bytes(b"%PDF-1.4\n% mutated\n")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["undo", "-y", "--session", first.timestamp, "--backup-dir", str(backup_root)],
    )
    assert result.exit_code == 0, result.stdout
    assert a.read_bytes().startswith(b"%PDF")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_undo.py -v -k "input_path or session_flag"`
Expected: at least one test fails because the locator hasn't been wired yet.

- [ ] **Step 3: Wire the locator into `undo.py`**

In `src/kuroi/cli/undo.py`, replace the block delimited by the `TODO(Task 8)` comment and the `console.print("  Restored.")` line with this locator-aware version:

```python
    from kuroi.core.backup import (
        find_session_by_path,
        find_session_by_timestamp,
    )

    bak = None
    if session is not None:
        bak = find_session_by_timestamp(backup_dir, session)
        if bak is None:
            console.print(f"  No backup at {backup_dir}/{session}.")
            console.print("  Run 'kuroi backups list' to see what exists.")
            raise typer.Exit(code=1)
    elif input is not None:
        bak = find_session_by_path(backup_dir, input)
        if bak is None:
            console.print(
                f"  No backup found for {input}. "
                "Was this run made with --no-backup?"
            )
            raise typer.Exit(code=1)
    else:
        bak = latest_backup(backup_dir)
        if bak is None:
            console.print(f"  No backup found in {backup_dir}.")
            raise typer.Exit(code=1)

    console.print(f"  Last backup: {bak.timestamp}")
    console.print(f"  Will restore: {bak.original_path}")

    if not yes:
        confirm = typer.confirm("Restore now?", default=True)
        if not confirm:
            raise typer.Exit(code=0)

    bak.original_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak.copy_path, bak.original_path)
    console.print("  Restored.")
```

(The `from … import …` line goes at the top of the function for now; we'll lift it to module scope when the file stabilizes.)

- [ ] **Step 4: Run the full undo test suite**

Run: `pytest tests/cli/test_undo.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/undo.py tests/cli/test_undo.py
git commit -m "feat(undo): resolve INPUT and --session to a specific backup

Without selectors, falls back to today's latest-overall behavior. With
INPUT, picks the newest backup matching that path. With --session, picks
the exact timestamped session. Missing matches return clear exit-1 errors.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Regeneration path (flag-driven, no picker yet)

**Files:**
- Modify: `src/kuroi/cli/undo.py`
- Modify: `tests/cli/test_undo.py`

This is the largest behavioral task. It introduces:
1. Loading the audit log when present.
2. Building the exclusion set from `--pages`/`--page`/`--kind`/`--words`.
3. Calling `apply_redactions` against the backup with the kept findings.
4. Running `verify_pdf` on the result.
5. Writing the `<undo_ts>.undo.jsonl`.
6. Acquiring `output_lock` for the move.
7. Backup integrity check (input_sha256).

- [ ] **Step 1: Write the end-to-end happy-path test**

Add the following imports to the **top** of `tests/cli/test_undo.py` (alongside
the existing imports — these are needed by `_seed_run` and the tests below):

```python
import hashlib
import json

from kuroi.core.audit import AuditLog
from kuroi.core.findings import Finding
from kuroi.core.redaction import apply_redactions
from kuroi.core.pdf import extract_word_index
```

Then append the helper and tests to the bottom of the file:

```python


def _seed_run(
    tmp_path: Path,
    pdf: Path,
    *,
    findings: list[Finding],
    backup_root: Path,
    audit_dir: Path,
) -> tuple[Path, str]:
    """Simulate a kuroi run: create backup, redact, write audit log."""
    backup_root.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    from kuroi.core.backup import create_backup

    bak = create_backup(pdf, backup_root=backup_root)
    extraction = extract_word_index(bak.copy_path)
    pages = extraction.pages
    out_path = pdf  # in-place
    apply_redactions(bak.copy_path, findings, pages, out_path)
    input_bytes = bak.copy_path.read_bytes()
    audit_path = audit_dir / f"{bak.timestamp}.jsonl"
    audit = AuditLog.open(
        audit_path,
        original=pdf,
        output=out_path,
        provider="test",
        model="test",
        rules=(),
        session_id="test-session-id",
        input_sha256=hashlib.sha256(input_bytes).hexdigest(),
        input_pages=len(pages),
        input_bytes=len(input_bytes),
        model_version="test",
    )
    for f in findings:
        page = pages[f.page - 1]
        words = page.words[f.start : f.end + 1]
        text = " ".join(w.text for w in words)
        audit.write_finding(f, redacted_text=text, context_text=text)
    audit.close(
        verification_passed=True,
        redaction_count=len(findings),
        output_sha256=hashlib.sha256(out_path.read_bytes()).hexdigest(),
    )
    return audit_path, bak.timestamp


def test_undo_regenerates_with_page_filter(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(
        ["alpha bravo charlie delta", "echo foxtrot golf hotel"],
        filename="doc.pdf",
    )
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"

    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
        Finding(page=1, start=2, end=2, kind="person", confidence="high", source="llm"),
        Finding(page=2, start=1, end=1, kind="email", confidence="high", source="llm"),
    ]
    audit_path, ts = _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--kind",
            "email",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout

    # An undo log was written
    undo_logs = list(audit_dir.glob("*.undo.jsonl"))
    assert len(undo_logs) == 1
    lines = [json.loads(l) for l in undo_logs[0].read_text().splitlines() if l]
    assert lines[0]["event"] == "undo_start"
    assert lines[0]["findings_excluded"] == 1
    finding_events = [l for l in lines if l["event"] == "undo_finding"]
    assert len(finding_events) == 1
    assert finding_events[0]["page"] == 1
    assert finding_events[0]["kind"] == "email"
    assert lines[-1]["event"] == "undo_end"
    assert lines[-1]["status"] == "ok"


def test_undo_empty_exclusion_set_exits_6(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--kind",
            "iban",  # nothing matches
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 6
    assert "nothing to undo" in result.stdout.lower()


def test_undo_backup_hash_mismatch_exits_1(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )
    # tamper with the backup
    sessions = list(backup_root.iterdir())
    backup_pdf = next(p for p in sessions[0].iterdir() if p.suffix == ".pdf")
    backup_pdf.write_bytes(b"%PDF-1.4\n% TAMPERED\n")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--kind",
            "email",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 1
    assert "backup file modified" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_undo.py -v -k "regenerates_with_page_filter or empty_exclusion_set or backup_hash_mismatch"`
Expected: failures — the regeneration path isn't implemented yet.

- [ ] **Step 3: Rewrite `undo.py` with the regeneration path**

Replace `src/kuroi/cli/undo.py` with:

```python
"""kuroi undo — restore from backup, optionally scoped to file/pages/elements."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.audit_replay import (
    ReplayableFinding,
    ReplayableSession,
    build_exclusion_set,
    load_session,
)
from kuroi.core.backup import (
    find_session_by_path,
    find_session_by_timestamp,
    latest_backup,
    sweep_backups,
)
from kuroi.core.config import (
    ConfigError,
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
    xdg_data_home,
)
from kuroi.core.findings import Finding
from kuroi.core.locks import LockHeldError, output_lock
from kuroi.core.page_selection import PageSelectionError
from kuroi.core.page_selection import parse as parse_pages
from kuroi.core.pdf import extract_word_index
from kuroi.core.redaction import apply_redactions
from kuroi.core.undo_audit import UndoAuditLog
from kuroi.core.verification import verify_pdf

console = Console()


def _parse_words(spec: str | None) -> tuple[int, int] | None:
    if spec is None:
        return None
    try:
        left, _, right = spec.partition("-")
        return (int(left), int(right))
    except (ValueError, AttributeError) as exc:
        raise typer.BadParameter(f"--words must be START-END (got {spec!r})") from exc


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_session_start(audit_path: Path) -> dict:
    """Read just the first NDJSON line, parsed."""
    with audit_path.open("r", encoding="utf-8") as fh:
        line = fh.readline().strip()
    return json.loads(line)


def _replayable_to_finding(rf: ReplayableFinding) -> Finding:
    return Finding(
        page=rf.page,
        start=rf.word_start,
        end=rf.word_end,
        kind=rf.kind,
        confidence=rf.confidence,
        source=rf.source,
    )


def _legacy_full_restore(
    bak,  # noqa: ANN001 — kuroi.core.backup.Backup, avoid extra import
    *,
    yes: bool,
) -> None:
    console.print(f"  Last backup: {bak.timestamp}")
    console.print(f"  Will restore: {bak.original_path}")
    if not yes:
        if not typer.confirm("Restore now?", default=True):
            raise typer.Exit(code=0)
    bak.original_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak.copy_path, bak.original_path)
    console.print("  Restored.")


def undo(
    input: Path | None = typer.Argument(
        None,
        help="PDF whose backup should be restored. Default: most recent backup overall.",
    ),
    yes: bool = typer.Option(False, "-y", help="Skip the restore confirmation."),
    backup_dir: Path | None = typer.Option(
        None,
        "--backup-dir",
        help="Backup directory [default: $XDG_DATA_HOME/kuroi/backups].",
    ),
    audit_dir: Path | None = typer.Option(
        None,
        "--audit-dir",
        help="Audit directory [default: $XDG_DATA_HOME/kuroi/audit].",
    ),
    session: str | None = typer.Option(
        None, "--session", help="Exact backup-session timestamp to undo."
    ),
    pages: str | None = typer.Option(
        None,
        "--pages",
        help="Restrict undo to a page range (same syntax as `kuroi run --pages`).",
    ),
    page: int | None = typer.Option(None, "--page", help="Single page filter."),
    kind: str | None = typer.Option(None, "--kind", help="Finding kind filter."),
    words: str | None = typer.Option(
        None, "--words", help="Word range filter START-END (requires --page)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan, write nothing."),
) -> None:
    """Restore an original PDF from a backup; supports file/page/element scoping."""

    # ---- Validation
    if words is not None and page is None:
        raise typer.BadParameter("--words requires --page")
    words_tuple = _parse_words(words)

    pages_tuple: tuple[int, ...] | None = None
    if pages is not None:
        try:
            pages_tuple = parse_pages(pages).pages
        except PageSelectionError as exc:
            raise typer.BadParameter(str(exc)) from exc

    has_element_selector = page is not None or kind is not None or words is not None

    # ---- Config + dirs
    if backup_dir is None:
        backup_dir = xdg_data_home() / "kuroi" / "backups"
    if audit_dir is None:
        audit_dir = xdg_data_home() / "kuroi" / "audit"

    try:
        config = resolve_config(
            ConfigOverrides(),
            env=os.environ,
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    sweep_backups(backup_dir, retention_hours=config.backup_retention_hours)

    # ---- Locate the session
    if session is not None:
        bak = find_session_by_timestamp(backup_dir, session)
        if bak is None:
            console.print(f"  No backup at {backup_dir}/{session}.")
            console.print("  Run 'kuroi backups list' to see what exists.")
            raise typer.Exit(code=1)
    elif input is not None:
        bak = find_session_by_path(backup_dir, input)
        if bak is None:
            console.print(
                f"  No backup found for {input}. "
                "Was this run made with --no-backup?"
            )
            raise typer.Exit(code=1)
    else:
        bak = latest_backup(backup_dir)
        if bak is None:
            console.print(f"  No backup found in {backup_dir}.")
            raise typer.Exit(code=1)

    audit_path = audit_dir / f"{bak.timestamp}.jsonl"
    audit_present = audit_path.is_file()

    # ---- Path A: no audit log, no selectors → legacy full restore
    if not audit_present and not has_element_selector and pages_tuple is None:
        _legacy_full_restore(bak, yes=yes)
        return

    # ---- Path B: missing audit log but selectors requested → exit 3
    if not audit_present and (has_element_selector or pages_tuple is not None):
        console.print(
            f"  Audit log not found for session {bak.timestamp}; "
            "element-level undo requires the audit log."
        )
        raise typer.Exit(code=3)

    # ---- Load the audit log
    try:
        session_obj = load_session(audit_path)
    except ValueError as exc:
        console.print(f"  Failed to parse audit log: {exc}")
        raise typer.Exit(code=3) from exc

    # ---- Backup integrity check
    session_start = _read_session_start(audit_path)
    expected_sha = session_start.get("input_sha256")
    actual_sha = _hash_file(bak.copy_path)
    if expected_sha and expected_sha != actual_sha:
        console.print(
            "  backup file modified since run — refusing to regenerate."
        )
        raise typer.Exit(code=1)

    # ---- Build exclusion set (flag path only; picker added in Task 10)
    excluded = build_exclusion_set(
        session_obj.findings,
        pages=pages_tuple,
        page=page,
        kind=kind,
        words=words_tuple,
        picker_indices=(),
    )

    if not excluded:
        console.print("  Nothing to undo (filters matched no findings).")
        raise typer.Exit(code=6)

    # ---- Confirmation summary
    console.print(
        f"  Will un-redact {len(excluded)} finding(s) "
        f"(regenerate {session_obj.output_path} from backup):"
    )
    for idx in sorted(excluded):
        f = session_obj.findings[idx]
        console.print(f"    p.{f.page}  {f.kind:<10}  (words {f.word_start}-{f.word_end})")
    if not yes:
        if not typer.confirm("Proceed?", default=True):
            raise typer.Exit(code=0)
    if dry_run:
        console.print("  Dry run: not writing.")
        return

    # ---- Regenerate
    output_path = session_obj.output_path
    kept = [
        _replayable_to_finding(rf)
        for i, rf in enumerate(session_obj.findings)
        if i not in excluded
    ]

    selector_payload = {
        "interactive": False,
        "pages": pages,
        "page": page,
        "kind": kind,
        "words": words,
    }
    undo_ts_path = audit_dir / f"{_undo_timestamp()}.undo.jsonl"
    undo_log = UndoAuditLog.open(
        undo_ts_path,
        source_session_id=session_obj.session_id,
        source_session_ts=bak.timestamp,
        source_audit_path=audit_path,
        input_path=session_obj.input_path,
        output_path=output_path,
        backup_path=bak.copy_path,
        selector=selector_payload,
        findings_total=len(session_obj.findings),
        findings_excluded=len(excluded),
    )

    # collect text_sha256 / context_sha256 for the undo_finding rows
    sha_by_index: dict[int, tuple[str, str]] = {}
    with audit_path.open("r", encoding="utf-8") as fh:
        idx_counter = 0
        for raw in fh:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "finding" and obj.get("decision") == "applied":
                if idx_counter in excluded:
                    sha_by_index[idx_counter] = (
                        obj.get("text_sha256", ""),
                        obj.get("context_sha256", ""),
                    )
                idx_counter += 1

    for idx in sorted(excluded):
        rf = session_obj.findings[idx]
        text_sha, context_sha = sha_by_index.get(idx, ("", ""))
        undo_log.write_undo_finding(
            rf, text_sha256=text_sha, context_sha256=context_sha
        )

    try:
        with output_lock(output_path):
            temp_out = output_path.with_suffix(
                output_path.suffix + ".kuroi-undo-tmp"
            )

            if not kept:
                # Excluding every finding → backup IS the answer.
                shutil.copy2(bak.copy_path, temp_out)
            else:
                extraction = extract_word_index(bak.copy_path)
                apply_redactions(bak.copy_path, kept, extraction.pages, temp_out)

            report = verify_pdf(temp_out)
            if not report.passed:
                temp_out.unlink(missing_ok=True)
                undo_log.close(
                    status="verify_failed",
                    redactions_kept=len(kept),
                    redactions_un_redacted=len(excluded),
                    verify_result="fail",
                    verify_leak_count=len(report.leaks),
                    output_sha256="",
                )
                console.print(
                    f"  [red]Verification FAILED.[/] "
                    f"{len(report.leaks)} leaks; output not written."
                )
                raise typer.Exit(code=4)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(temp_out), str(output_path))
            new_sha = _hash_file(output_path)

        undo_log.close(
            status="ok",
            redactions_kept=len(kept),
            redactions_un_redacted=len(excluded),
            verify_result="pass",
            verify_leak_count=0,
            output_sha256=new_sha,
        )
        console.print(f"  Regenerated {output_path}")
        console.print(f"  Undo audit: {undo_ts_path}")
    except LockHeldError as exc:
        undo_log.close(
            status="failed",
            redactions_kept=0,
            redactions_un_redacted=0,
            verify_result="skipped",
            verify_leak_count=0,
            output_sha256="",
        )
        console.print(f"  [red]Lock held:[/] {exc}")
        raise typer.Exit(code=5) from exc
    except typer.Exit:
        raise
    except BaseException:
        undo_log.close(
            status="failed",
            redactions_kept=0,
            redactions_un_redacted=0,
            verify_result="skipped",
            verify_leak_count=0,
            output_sha256="",
        )
        raise


def _undo_timestamp() -> str:
    from kuroi.core.backup import session_timestamp

    return session_timestamp()
```

- [ ] **Step 4: Run the full undo test suite**

Run: `pytest tests/cli/test_undo.py -v`
Expected: every test passes (existing back-compat + the three new ones).

- [ ] **Step 5: Add lock + verification-failure tests**

Append to `tests/cli/test_undo.py`:

```python
def test_undo_output_lock_held_exits_5(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )
    # Pre-create the lock file
    lock = pdf.with_suffix(pdf.suffix + ".kuroi.lock")
    lock.write_bytes(b"")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--kind",
            "email",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 5


def test_undo_verification_failure_exits_4(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )

    from kuroi.core.verification import VerificationReport, Leak

    def fake_verify(_p):
        return VerificationReport(
            passed=False,
            leaks=(Leak(page=1, kind="text_under_overlay", detail="x"),),
        )

    monkeypatch.setattr("kuroi.cli.undo.verify_pdf", fake_verify)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--kind",
            "email",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 4
    assert "verification failed" in result.stdout.lower()
```

- [ ] **Step 6: Run the new tests**

Run: `pytest tests/cli/test_undo.py -v -k "lock_held or verification_failure"`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add src/kuroi/cli/undo.py tests/cli/test_undo.py
git commit -m "feat(undo): regenerate output from backup using exclusion-set filters

Implements the flag-driven regeneration path: load audit log, build the
exclusion set from --pages/--page/--kind/--words, re-run apply_redactions
against the backup, verify, atomic move under output_lock. Writes a new
<undo_ts>.undo.jsonl. Exit codes 1/3/4/5/6 wired.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Picker integration

**Files:**
- Modify: `src/kuroi/cli/undo.py`
- Modify: `tests/cli/test_undo.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_undo.py`:

```python
def test_undo_invokes_picker_when_tty_and_no_element_selectors(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
        Finding(page=1, start=1, end=1, kind="person", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "kuroi.cli.undo.pick_findings",
        lambda session, text_by_index, pages_filter: (0,),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    undo_logs = list(audit_dir.glob("*.undo.jsonl"))
    assert len(undo_logs) == 1
    lines = [json.loads(l) for l in undo_logs[0].read_text().splitlines() if l]
    assert lines[0]["findings_excluded"] == 1
    assert lines[0]["selector"]["interactive"] is True


def test_undo_non_tty_no_selector_falls_back_to_full_restore(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    # picker_calls should not be invoked
    def boom(*a, **kw):
        raise AssertionError("picker called in non-TTY mode")

    monkeypatch.setattr("kuroi.cli.undo.pick_findings", boom)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # No element-selector → backup contents now in pdf
    sessions = list(backup_root.iterdir())
    backup_pdf = next(p for p in sessions[0].iterdir() if p.suffix == ".pdf")
    assert pdf.read_bytes() == backup_pdf.read_bytes()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_undo.py -v -k "invokes_picker or non_tty_no_selector"`
Expected: failures — picker isn't wired in yet.

- [ ] **Step 3: Wire the picker into `undo.py`**

Add this import at the top of `src/kuroi/cli/undo.py` (with the other kuroi imports):

```python
import sys

from kuroi.cli.undo_picker import pick_findings
```

Then, in the `undo` function, **after** the audit log is loaded and the integrity check passes, **but before** `build_exclusion_set` is called, insert this picker invocation:

```python
    # ---- Decide whether to open the picker
    open_picker = (
        sys.stdin.isatty()
        and not yes
        and not has_element_selector
    )
    picker_indices: tuple[int, ...] = ()
    selector_interactive = False
    if open_picker:
        text_by_index = _reconstruct_text(bak.copy_path, session_obj.findings)
        try:
            picker_indices = pick_findings(
                session_obj,
                text_by_index=text_by_index,
                pages_filter=pages_tuple,
            )
        except KeyboardInterrupt:
            raise typer.Exit(code=130) from None
        selector_interactive = True
```

Then update the `build_exclusion_set` call to pass `picker_indices`:

```python
    excluded = build_exclusion_set(
        session_obj.findings,
        pages=pages_tuple,
        page=page,
        kind=kind,
        words=words_tuple,
        picker_indices=picker_indices,
    )
```

And update `selector_payload`:

```python
    selector_payload = {
        "interactive": selector_interactive,
        "pages": pages,
        "page": page,
        "kind": kind,
        "words": words,
    }
```

Finally, add the text-reconstruction helper near the top of `undo.py` (above `def undo(...)`):

```python
def _reconstruct_text(
    backup_pdf: Path,
    findings: tuple[ReplayableFinding, ...],
) -> dict[int, str]:
    """Re-extract words from the backup so the picker can show real text."""
    pages_by_number = {p.number: p for p in extract_word_index(backup_pdf).pages}
    text_by_index: dict[int, str] = {}
    for idx, f in enumerate(findings):
        page = pages_by_number.get(f.page)
        if page is None:
            text_by_index[idx] = ""
            continue
        words = page.words[f.word_start : f.word_end + 1]
        text_by_index[idx] = " ".join(w.text for w in words)
    return text_by_index
```

Also, in the no-audit-log branch — the legacy full restore — update the trigger condition to honor TTY:

```python
    if not audit_present and not has_element_selector and pages_tuple is None:
        _legacy_full_restore(bak, yes=yes)
        return
```

(no change here; just confirming.)

When audit log IS present but no selectors and stdin is NOT a TTY, we want the regenerate-from-backup full restore (which writes to `audit.output_path`). To achieve that without picker, after the picker block, add:

```python
    # No selectors AND non-TTY (so picker was skipped) → treat as "exclude every finding".
    if not has_element_selector and pages_tuple is None and not picker_indices and not selector_interactive:
        excluded = frozenset(range(len(session_obj.findings)))
```

Place this immediately AFTER the picker block and BEFORE the `build_exclusion_set` call. Then the subsequent flow proceeds normally (with the all-exclude short-circuit copying the backup to `output_path`).

- [ ] **Step 4: Run the full undo test suite**

Run: `pytest tests/cli/test_undo.py -v`
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/undo.py tests/cli/test_undo.py
git commit -m "feat(undo): open the interactive picker for TTY undo without selectors

When stdin is a TTY, no -y, and no element-selector flags, pick_findings
opens a multi-select over reconstructed text from the backup. --pages
still composes (pre-filters the picker). Non-TTY non-selector path uses
the audit log's output_path with the full-exclude short-circuit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Dry-run, exclude-all short-circuit, and final polish

**Files:**
- Modify: `src/kuroi/cli/undo.py`
- Modify: `tests/cli/test_undo.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cli/test_undo.py`:

```python
def test_undo_dry_run_writes_nothing(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )

    before_mtime = pdf.stat().st_mtime_ns
    before_bytes = pdf.read_bytes()

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--kind",
            "email",
            "--dry-run",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert pdf.stat().st_mtime_ns == before_mtime
    assert pdf.read_bytes() == before_bytes
    assert list(audit_dir.glob("*.undo.jsonl")) == []


def test_undo_exclude_all_short_circuits_to_backup_copy(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["alpha bravo"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    findings = [
        Finding(page=1, start=0, end=0, kind="email", confidence="high", source="llm"),
        Finding(page=1, start=1, end=1, kind="person", confidence="high", source="llm"),
    ]
    _seed_run(
        tmp_path, pdf,
        findings=findings, backup_root=backup_root, audit_dir=audit_dir,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "undo",
            str(pdf),
            "-y",
            "--page",
            "1",
            "--backup-dir",
            str(backup_root),
            "--audit-dir",
            str(audit_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    sessions = list(backup_root.iterdir())
    backup_pdf = next(p for p in sessions[0].iterdir() if p.suffix == ".pdf")
    assert pdf.read_bytes() == backup_pdf.read_bytes()


def test_undo_no_backup_message_mentions_no_backup_flag(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    pdf = make_pdf(["x"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    backup_root.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["undo", str(pdf), "-y", "--backup-dir", str(backup_root)],
    )
    assert result.exit_code == 1
    assert "--no-backup" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/cli/test_undo.py -v -k "dry_run or exclude_all or no_backup_message"`
Expected: failures — short-circuits and dry-run aren't fully wired yet.

- [ ] **Step 3: Adjust the implementation**

In `src/kuroi/cli/undo.py`, move the `--dry-run` early-return to happen **before** the undo log is opened (so dry-run truly writes nothing). Find the block:

```python
    if dry_run:
        console.print("  Dry run: not writing.")
        return
```

…and move it to right after the confirmation prompt resolves (still before `UndoAuditLog.open(...)`):

```python
    if not yes:
        if not typer.confirm("Proceed?", default=True):
            raise typer.Exit(code=0)
    if dry_run:
        console.print("  Dry run: not writing.")
        return

    # ---- Regenerate (and only now do we open the undo log)
    output_path = session_obj.output_path
    ...
```

- [ ] **Step 4: Run the full undo test suite**

Run: `pytest tests/cli/test_undo.py -v`
Expected: every test passes.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src tests && uv run ruff format src tests`
Expected: no lint errors; formatter applies any pending changes.

- [ ] **Step 6: Type-check the new modules**

Run: `uv run mypy src/kuroi/core/audit_replay.py src/kuroi/core/undo_audit.py src/kuroi/cli/undo_picker.py src/kuroi/cli/undo.py`
Expected: no type errors. (If mypy isn't part of this project's tooling, skip; check `pyproject.toml` first.)

- [ ] **Step 7: Run the entire test suite**

Run: `uv run pytest -x`
Expected: all tests pass; no regressions in unrelated modules.

- [ ] **Step 8: Commit**

```bash
git add src/kuroi/cli/undo.py tests/cli/test_undo.py
git commit -m "feat(undo): finalize dry-run, exclude-all short-circuit, and error wording

Dry-run returns before opening the undo log; exclude-all skips the
PyMuPDF regenerate call and copies the backup directly; missing-backup
errors call out --no-backup as a likely cause. Lint/format applied.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review checklist

After all 11 tasks are complete, walk through this list:

**Spec coverage:**

- [x] File scoping → Tasks 6, 7, 8 (locator + INPUT positional).
- [x] Page range → Tasks 7, 9 (--pages parses, fed to build_exclusion_set).
- [x] Element selection (flags) → Tasks 7, 9 (--page/--kind/--words).
- [x] Element selection (picker) → Tasks 4, 5, 10.
- [x] Regenerate-from-backup mechanism → Task 9.
- [x] Backup integrity check → Task 9.
- [x] Output lock → Task 9.
- [x] Verification gate → Task 9.
- [x] Undo audit log → Tasks 3, 9, 10.
- [x] Short-circuits (no selector + audit, no selector + no audit, exclude-all, empty) → Tasks 9, 10, 11.
- [x] Confirmation summary + dry-run → Tasks 9, 11.
- [x] Exit codes 1/2/3/4/5/6/130 → all wired across Tasks 7–11.

**No placeholders**: every step shows actual test code or implementation code; no "TBD" or "implement later".

**Type consistency**: `ReplayableFinding(page, word_start, word_end, kind, confidence, source, bbox)` is referenced identically in Tasks 1, 2, 3, 5, 9, 10. `pick_findings(session, text_by_index, pages_filter)` signature is identical in Tasks 5 and 10. `UndoAuditLog.open(...)` / `write_undo_finding(...)` / `close(...)` are defined in Task 3 and used in Task 9.
