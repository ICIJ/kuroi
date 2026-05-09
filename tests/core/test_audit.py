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
    assert footer["status"] == "ok"
    assert footer["redactions_applied"] == 1


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


def test_audit_session_end_carries_full_metrics(tmp_path: Path) -> None:
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
        input_bytes=100,
        model_version="claude-opus-4-7",
    )
    log.close(
        verification_passed=True,
        redaction_count=5,
        tokens_in=1000,
        tokens_out=200,
        cost_usd=0.0285,
        output_sha256="e" * 64,
        verify_leak_count=0,
        redactions_rejected=2,
        redactions_excluded=1,
    )

    footer = json.loads(log_path.read_text().splitlines()[-1])
    assert footer["event"] == "session_end"
    assert footer["status"] == "ok"
    assert footer["tokens_in"] == 1000
    assert footer["tokens_out"] == 200
    assert footer["cost_usd"] == 0.0285
    assert footer["output_sha256"] == "e" * 64
    assert footer["verify_result"] == "pass"
    assert footer["verify_leak_count"] == 0
    assert footer["redactions_applied"] == 5
    assert footer["redactions_rejected"] == 2
    assert footer["redactions_excluded"] == 1
    assert "duration_ms" in footer


def test_audit_finding_carries_bbox_and_hashes(tmp_path: Path) -> None:
    log_path = tmp_path / "f.jsonl"
    log = AuditLog.open(
        log_path,
        original=tmp_path / "i.pdf",
        output=tmp_path / "o.pdf",
        provider="anthropic", model="m", rules=(),
        session_id="x", input_sha256="0" * 64,
        input_pages=1, input_bytes=1, model_version="m",
    )
    log.write_finding(
        Finding(page=1, start=5, end=6, kind="email", confidence="high",
                source="rules:pii-en"),
        bbox=(10.0, 20.0, 30.0, 40.0),
        redacted_text="x@y.z",
        context_text="see x@y.z for contact",
    )
    log.close(verification_passed=True, redaction_count=1)

    finding = json.loads(log_path.read_text().splitlines()[1])
    assert finding["event"] == "finding"
    assert finding["bbox"] == [10.0, 20.0, 30.0, 40.0]
    assert finding["text_length"] == 5
    assert len(finding["text_sha256"]) == 64
    assert len(finding["context_sha256"]) == 64
    assert finding["decision"] == "applied"
    assert finding["reviewer"] == "auto"
    # Plaintext fields default off:
    assert "text" not in finding
    assert "context" not in finding


def test_instruction_decomposed_event_serializes(tmp_path: Path) -> None:
    """The instruction_decomposed event must round-trip through AuditLog
    with all four fields preserved (source, detail, rule_count, rules)."""
    log_path = tmp_path / "test.jsonl"
    log = AuditLog.open(
        log_path,
        original=tmp_path / "in.pdf",
        output=tmp_path / "out.pdf",
        provider="ollama",
        model="llama3.1:8b",
        rules=(),
        session_id="abc",
        input_sha256="0" * 64,
        input_pages=1,
        input_bytes=100,
        model_version="llama3.1:8b",
        instructions=({"text": "Redact stuff"},),
        config_resolved_from=(),
    )
    log.write_event(
        "instruction_decomposed",
        source="parser",
        detail="parser split into 5 rules",
        rule_count=5,
        rules=["1. A", "2. B", "3. C", "4. D", "5. E"],
    )
    log.close(
        verification_passed=True,
        redaction_count=0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        output_sha256="0" * 64,
    )

    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    decomp = next(e for e in events if e.get("event") == "instruction_decomposed")
    assert decomp["source"] == "parser"
    assert decomp["detail"] == "parser split into 5 rules"
    assert decomp["rule_count"] == 5
    assert decomp["rules"] == ["1. A", "2. B", "3. C", "4. D", "5. E"]
