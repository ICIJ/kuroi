import json
import os
import stat
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
    )
    log.write_finding(
        Finding(page=1, start=5, end=6, kind="email", confidence="high", source="rules:pii-en")
    )
    log.close(verification_passed=True, redaction_count=1)

    lines = log_path.read_text().splitlines()
    header = json.loads(lines[0])
    finding = json.loads(lines[1])
    footer = json.loads(lines[-1])

    assert header["event"] == "session_open"
    assert header["provider"] == "anthropic"
    assert finding["event"] == "finding"
    assert finding["kind"] == "email"
    assert footer["event"] == "session_close"
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
    )
    log.close(verification_passed=True, redaction_count=0)

    mode = stat.S_IMODE(os.stat(log_path).st_mode)
    assert mode == 0o600
