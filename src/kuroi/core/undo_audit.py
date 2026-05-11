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
    ) -> UndoAuditLog:
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
