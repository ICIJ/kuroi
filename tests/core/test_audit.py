import json
import os
import stat
import uuid
from pathlib import Path

from kuroi.core.audit import AuditLog
from kuroi.core.findings import Finding


def test_audit_log_writes_session_header_and_findings(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log = AuditLog.open(
        log_path,
        original=tmp_path / "input.pdf",
        output=tmp_path / "output.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=("pii-en",),
        session_id="x",
        input_sha256="0" * 64,
        input_pages=1,
        input_bytes=1,
        model_version="claude-opus-4-7",
    )
    log.write_finding(
        Finding(page=1, start=5, end=6, kind="email", confidence="high", source="rules:pii-en")
    )
    log.close(verification_passed=True, redaction_count=1)

    lines = log_path.read_text().splitlines()
    header = json.loads(lines[0])
    finding = json.loads(lines[1])
    footer = json.loads(lines[-1])

    assert header["event"] == "session_start"
    assert header["provider"] == "anthropic"
    assert finding["event"] == "finding"
    assert finding["kind"] == "email"
    assert footer["event"] == "session_end"
    assert footer["verification_passed"] is True
    assert footer["redaction_count"] == 1


def test_audit_log_file_is_mode_0600(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log = AuditLog.open(
        log_path,
        original=tmp_path / "in.pdf",
        output=tmp_path / "out.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=(),
        session_id="x",
        input_sha256="0" * 64,
        input_pages=1,
        input_bytes=1,
        model_version="claude-opus-4-7",
    )
    log.close(verification_passed=True, redaction_count=0)

    mode = stat.S_IMODE(os.stat(log_path).st_mode)
    assert mode == 0o600


def test_audit_session_start_includes_full_provenance(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    sid = str(uuid.uuid4())
    log = AuditLog.open(
        log_path,
        original=tmp_path / "input.pdf",
        output=tmp_path / "output.pdf",
        provider="anthropic",
        model="claude-opus-4-7",
        rules=("pii-en",),
        session_id=sid,
        input_sha256="3a7f" + "0" * 60,
        input_pages=12,
        input_bytes=1234567,
        model_version="claude-opus-4-7@2026-04-15",
        instructions=(),
        config_resolved_from=("flag", "user_config"),
    )
    log.close(verification_passed=True, redaction_count=0)

    header = json.loads(log_path.read_text().splitlines()[0])
    assert header["event"] == "session_start"
    assert header["audit_schema_version"] == 1
    assert header["session_id"] == sid
    assert header["input_sha256"] == "3a7f" + "0" * 60
    assert header["input_pages"] == 12
    assert header["input_bytes"] == 1234567
    assert header["model_version"] == "claude-opus-4-7@2026-04-15"
    assert header["instructions"] == []
    assert header["rules"] == ["pii-en"]
    assert header["config_resolved_from"] == ["flag", "user_config"]
