"""Append-only NDJSON audit log written 0600.

Each `kuroi run` opens one log file. The file contains a session_start header,
one finding line per applied redaction, and a session_end footer recording
verification status. Closure on Python exit even if the process crashes mid-run
is the caller's responsibility (use the context-manager form).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from kuroi.core.findings import Finding


class AuditLog:
    def __init__(self, fh: IO[str]) -> None:
        self._fh = fh
        self._started = time.monotonic()

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
        session_id: str,
        input_sha256: str,
        input_pages: int,
        input_bytes: int,
        model_version: str,
        instructions: tuple[dict[str, Any], ...] = (),
        config_resolved_from: tuple[str, ...] = (),
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
                "event": "session_start",
                "audit_schema_version": 1,
                "session_id": session_id,
                "ts_start": _now_iso(),
                "kuroi_version": _kuroi_version(),
                "input_path": str(original),
                "input_sha256": input_sha256,
                "input_pages": input_pages,
                "input_bytes": input_bytes,
                "output_path": str(output),
                "provider": provider,
                "model": model,
                "model_version": model_version,
                "rules": list(rules),
                "instructions": list(instructions),
                "config_resolved_from": list(config_resolved_from),
            }
        )
        return log

    def write_finding(
        self,
        finding: Finding,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        redacted_text: str = "",
        context_text: str = "",
        decision: str = "applied",
        reviewer: str = "auto",
        include_text: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "finding",
            "ts": _now_iso(),
            "page": finding.page,
            "word_start": finding.start,
            "word_end": finding.end,
            "kind": finding.kind,
            "confidence": finding.confidence,
            "source": finding.source,
            "bbox": list(bbox) if bbox is not None else None,
            "text_length": len(redacted_text),
            "text_sha256": hashlib.sha256(redacted_text.encode("utf-8")).hexdigest(),
            "context_sha256": hashlib.sha256(context_text.encode("utf-8")).hexdigest(),
            "decision": decision,
            "reviewer": reviewer,
        }
        if include_text:
            payload["text"] = redacted_text
            payload["context"] = context_text
        self._write(payload)

    def write_event(self, event: str, **fields: Any) -> None:
        self._write({"event": event, "ts": _now_iso(), **fields})

    def close(
        self,
        *,
        verification_passed: bool,
        redaction_count: int,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        output_sha256: str = "",
        verify_leak_count: int = 0,
        redactions_rejected: int = 0,
        redactions_excluded: int = 0,
    ) -> None:
        duration_ms = int((time.monotonic() - self._started) * 1000)
        if verification_passed:
            status, verify_result = "ok", "pass"
        elif verify_leak_count > 0:
            status, verify_result = "verify_failed", "fail"
        else:
            status, verify_result = "failed", "skipped"
        self._write(
            {
                "event": "session_end",
                "ts_end": _now_iso(),
                "status": status,
                "redactions_applied": redaction_count,
                "redactions_rejected": redactions_rejected,
                "redactions_excluded": redactions_excluded,
                "duration_ms": duration_ms,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
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
