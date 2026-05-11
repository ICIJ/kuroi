"""Tests for the undo audit log."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

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
    finding_line = next(ln for ln in lines if ln["event"] == "undo_finding")
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
