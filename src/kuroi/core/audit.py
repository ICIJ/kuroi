"""Append-only NDJSON audit log written 0600.

Each `kuroi run` opens one log file. The file contains a session_open header,
one finding line per applied redaction, and a session_close footer recording
verification status. Closure on Python exit even if the process crashes mid-run
is the caller's responsibility (use the context-manager form).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from kuroi.core.findings import Finding


class AuditLog:
    def __init__(self, fh: IO[str]) -> None:
        self._fh = fh

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        original: Path,
        output: Path,
        provider: str,
        model: str,
        rules: tuple[str, ...],
    ) -> AuditLog:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir's mode is subject to umask; force the right mode after creation.
        os.chmod(str(path.parent), 0o700)
        # Open with restrictive mode from the start to avoid a brief permissive window.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        fh = os.fdopen(fd, "w", encoding="utf-8")
        log = cls(fh)
        log._write(
            {
                "event": "session_open",
                "ts": _now_iso(),
                "original": str(original),
                "output": str(output),
                "provider": provider,
                "model": model,
                "rules": list(rules),
            }
        )
        return log

    def write_finding(self, finding: Finding) -> None:
        self._write({"event": "finding", "ts": _now_iso(), **asdict(finding)})

    def write_event(self, event: str, **fields: Any) -> None:
        self._write({"event": event, "ts": _now_iso(), **fields})

    def close(self, *, verification_passed: bool, redaction_count: int) -> None:
        self._write(
            {
                "event": "session_close",
                "ts": _now_iso(),
                "verification_passed": verification_passed,
                "redaction_count": redaction_count,
            }
        )
        self._fh.close()

    def _write(self, payload: dict[str, Any]) -> None:
        self._fh.write(json.dumps(payload, separators=(",", ":")))
        self._fh.write("\n")
        self._fh.flush()


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
