import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kuroi.cli import app
from kuroi.core.audit import AuditLog
from kuroi.core.backup import create_backup
from kuroi.core.findings import Finding
from kuroi.core.pdf import extract_word_index
from kuroi.core.redaction import apply_redactions


def test_undo_restores_latest_backup(make_pdf: Callable[..., Path], tmp_path: Path) -> None:
    original = make_pdf(["original body"], filename="doc.pdf")
    backup_root = tmp_path / "backups"
    create_backup(original, backup_root=backup_root)

    # Now mutate the original, simulating a redaction we want to undo
    original.write_bytes(b"%PDF-1.4\n% mutated\n")

    runner = CliRunner()
    result = runner.invoke(app, ["undo", "-y", "--backup-dir", str(backup_root)])

    assert result.exit_code == 0
    assert original.read_text(errors="ignore").strip() != "%PDF-1.4\n% mutated"
    # Should have a real PDF header byte sequence again
    assert original.read_bytes().startswith(b"%PDF")


def test_undo_with_no_backup_exits_1(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    runner = CliRunner()
    result = runner.invoke(app, ["undo", "-y", "--backup-dir", str(backup_root)])
    assert result.exit_code == 1
    assert "no backup" in result.stdout.lower()


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


def test_undo_with_input_path_picks_matching_backup(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
) -> None:
    a = make_pdf(["AAA"], filename="a.pdf")
    b = make_pdf(["BBB"], filename="b.pdf")
    backup_root = tmp_path / "backups"
    create_backup(a, backup_root=backup_root)
    time.sleep(1.1)  # Ensure different timestamp seconds so b is unambiguously newer
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

    from kuroi.core.verification import Leak, VerificationReport

    def fake_verify(_p):
        return VerificationReport(
            passed=False,
            leaks=(Leak(page=1, kind="text_under_overlay", bbox=None, recovered_text="x", detail="x"),),
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
