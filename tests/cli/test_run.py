import json as _json2
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pymupdf
import pytest
from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


@pytest.fixture
def stub_anthropic_client(monkeypatch) -> dict[str, Any]:
    """Patch AnthropicProvider to use a stub client returning no LLM findings."""
    from dataclasses import dataclass

    @dataclass
    class _Block:
        text: str

    @dataclass
    class _Response:
        content: list[_Block]

    class _Messages:
        def create(self, **kwargs: Any) -> _Response:
            return _Response(content=[_Block(text='{"findings": []}')])

    class _Client:
        def __init__(self) -> None:
            self.messages = _Messages()

    captured: dict[str, Any] = {}

    def _fake_init(
        self: Any,
        *,
        model: str = "claude-opus-4-7",
        api_key: str | None = None,
        client: Any | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.name = "anthropic"
        self.model = model
        self._max_tokens = max_tokens
        self._client = client or _Client()
        captured["instance"] = self

    monkeypatch.setattr("kuroi.providers.anthropic.AnthropicProvider.__init__", _fake_init)
    return captured


def test_run_redacts_emails_via_regex_rule(
    make_pdf: Callable[..., Path], tmp_path: Path, stub_anthropic_client: dict[str, Any]
) -> None:
    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "redacted.pdf"
    backup_dir = tmp_path / "backups"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    redacted = pymupdf.open(str(out))
    text = redacted[0].get_text("text")
    redacted.close()
    assert "alice@example.com" not in text
    assert "Contact" in text and "today" in text
    flat = "".join(result.stdout.split())
    assert "Backup:" in result.stdout
    sessions = [p for p in backup_dir.iterdir() if p.is_dir()]
    assert len(sessions) == 1
    assert "".join(str(sessions[0] / pdf.name).split()) in flat


def test_run_refuses_to_overwrite_input(
    make_pdf: Callable[..., Path], tmp_path: Path, stub_anthropic_client: dict[str, Any]
) -> None:
    pdf = make_pdf(["Contact alice@example.com today"])

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(pdf),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 2
    assert "overwrite" in result.stdout.lower()


def test_run_aborts_when_verification_fails(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch,
) -> None:
    # Force verification to claim a leak so the gate trips.
    from kuroi.core.verification import Leak, VerificationReport

    def _always_fail(_: Path) -> VerificationReport:
        return VerificationReport(
            passed=False,
            leaks=(
                Leak(
                    page=1,
                    kind="text_under_overlay",
                    bbox=None,
                    recovered_text="leak",
                    detail="forced",
                ),
            ),
        )

    monkeypatch.setattr("kuroi.cli.run.verify_pdf", _always_fail)

    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "redacted.pdf"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 4
    assert not out.exists()


def test_run_seed_flag_anthropic_prints_not_honored_notice(monkeypatch, make_pdf, tmp_path):
    """When --seed is set against Anthropic, kuroi prints a one-line notice."""
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from kuroi.providers import anthropic as ap

    class _StubResp:
        content: ClassVar = [type("B", (), {"text": '{"findings": []}'})()]
        usage: ClassVar = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
        system_fingerprint = None

    class _StubClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs: Any) -> _StubResp:
                return _StubResp()

    real_provider = ap.AnthropicProvider
    monkeypatch.setattr(
        ap,
        "AnthropicProvider",
        lambda **kw: real_provider(client=_StubClient(), model=kw.get("model", "claude-opus-4-7")),
    )

    pdf = make_pdf(["dummy text"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "--seed",
            "42",
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )
    assert "--seed recorded but only temperature=0 is enforced" in result.stdout


def test_run_displays_pre_flight_cost_estimate(monkeypatch, make_pdf, tmp_path):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    from kuroi.providers import anthropic as ap

    class _StubResp:
        content: ClassVar = [type("B", (), {"text": '{"findings": []}'})()]
        usage: ClassVar = type("U", (), {"input_tokens": 100, "output_tokens": 20})()
        system_fingerprint = None

    class _StubClient:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kwargs: Any) -> _StubResp:
                return _StubResp()

    real_provider = ap.AnthropicProvider
    monkeypatch.setattr(
        ap,
        "AnthropicProvider",
        lambda **kw: real_provider(client=_StubClient(), model=kw.get("model", "claude-opus-4-7")),
    )

    pdf = make_pdf(["short"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )
    assert "Estimated cost" in result.stdout
    assert "$" in result.stdout


def test_run_writes_chunk_request_audit_event(monkeypatch, make_pdf, tmp_path):
    monkeypatch.setenv("KUROI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    audit_dir = tmp_path / "audit"

    class _StubResp2:
        content: ClassVar = [type("B", (), {"text": '{"findings": []}'})()]
        usage: ClassVar = type("U", (), {"input_tokens": 50, "output_tokens": 5})()
        system_fingerprint = None

    class _StubMessages:
        def create(self, **kwargs: Any) -> _StubResp2:
            return _StubResp2()

    class _StubClient:
        def __init__(self) -> None:
            self.messages = _StubMessages()

    def _fake_init(
        self: Any,
        *,
        model: str = "claude-opus-4-7",
        api_key: str | None = None,
        client: Any | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.name = "anthropic"
        self.model = model
        self._max_tokens = max_tokens
        self._client = client or _StubClient()

    monkeypatch.setattr("kuroi.providers.anthropic.AnthropicProvider.__init__", _fake_init)

    pdf = make_pdf(["alice@example.com"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "-o",
            str(out),
            "-y",
            "--audit-dir",
            str(audit_dir),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--rules",
            "pii",
        ],
    )
    assert result.exit_code == 0, result.stdout

    files = list(audit_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    chunk_lines = [
        _json2.loads(line) for line in lines if _json2.loads(line).get("event") == "chunk_request"
    ]
    assert len(chunk_lines) == 1
    assert chunk_lines[0]["tokens_in"] == 50
    assert chunk_lines[0]["tokens_out"] == 5
    assert chunk_lines[0]["seed_honored"] is False


def test_run_sweeps_expired_backups(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_name = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%dT%H-%M-%SZ-deadbe")
    (backup_dir / old_name).mkdir()
    (backup_dir / old_name / "manifest.json").write_text("{}")

    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert not (backup_dir / old_name).exists()


def test_run_refuses_existing_output_without_overwrite(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    pdf = make_pdf(["x"])
    out = tmp_path / "out.pdf"
    out.write_bytes(b"existing")

    result = runner.invoke(app, ["run", str(pdf), "--rules", "pii", "-o", str(out)])

    assert result.exit_code == 2
    assert "out.v2.pdf" in result.stdout


def test_run_overwrite_replaces_existing(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "out.pdf"
    out.write_bytes(b"existing")

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--overwrite",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out.stat().st_size > 8


def test_run_in_place_writes_to_input_and_keeps_backup(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    pdf = make_pdf(["Contact alice@example.com today"])
    backup_dir = tmp_path / "backups"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "--in-place",
            "-y",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert pdf.exists()
    sessions = [p for p in backup_dir.iterdir() if p.is_dir()]
    assert len(sessions) == 1


def test_run_in_place_with_output_flag_is_usage_error(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    pdf = make_pdf(["x"])
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(tmp_path / "out.pdf"),
            "--in-place",
        ],
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stdout


def test_run_held_lock_refuses(make_pdf: Callable[..., Path], tmp_path: Path) -> None:
    pdf = make_pdf(["x"])
    out = tmp_path / "out.pdf"
    lock_path = out.with_suffix(out.suffix + ".kuroi.lock")
    lock_path.write_text("")  # someone else's lock

    result = runner.invoke(app, ["run", str(pdf), "--rules", "pii", "-o", str(out)])
    assert result.exit_code == 2
    assert "another kuroi run" in result.stdout


def test_run_instruct_flag_passes_instruction_to_provider(
    make_pdf: Callable[..., Path], tmp_path: Path, monkeypatch
) -> None:
    """--instruct with no --rules skips rule loading and passes instruction to provider."""
    captured: dict[str, Any] = {}

    from kuroi.providers import anthropic as ap

    def _spy_detect(
        self: Any,
        pages: Any,
        llm_category_ids: Any,
        *,
        instructions: Any = (),
        seed: Any = None,
        attempt: int = 0,
    ) -> Any:
        from kuroi.core.audit_records import ChunkRecord

        captured["instructions"] = instructions
        captured["llm_category_ids"] = llm_category_ids
        return [], [
            ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=1,
                tokens_out=1,
                duration_ms=1,
            )
        ]

    monkeypatch.setattr(ap.AnthropicProvider, "detect_redactions", _spy_detect)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pdf = make_pdf(["Alice Smith, IP: 10.0.0.1"])
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact names and IP addresses",
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    # No findings → exits 0 with "No redactions proposed" message
    assert result.exit_code == 0, result.stdout
    assert captured.get("instructions") == ("redact names and IP addresses",)
    assert captured.get("llm_category_ids") == ()


def test_run_no_rules_no_instruct_noninteractive_errors(
    make_pdf: Callable[..., Path], tmp_path: Path, stub_anthropic_client: dict[str, Any]
) -> None:
    """-y with no --rules and no --instruct prints an error and exits 2."""
    pdf = make_pdf(["Alice Smith"])
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )
    assert result.exit_code == 2
    assert "--rules" in result.stdout or "--instruct" in result.stdout


def test_run_both_rules_and_instruct_run_together(
    make_pdf: Callable[..., Path], tmp_path: Path, monkeypatch
) -> None:
    """--rules and --instruct together: regex rules run AND instruction passed to LLM."""
    captured: dict[str, Any] = {}

    from kuroi.providers import anthropic as ap

    def _spy_detect(
        self: Any,
        pages: Any,
        llm_category_ids: Any,
        *,
        instructions: Any = (),
        seed: Any = None,
        attempt: int = 0,
    ) -> Any:
        from kuroi.core.audit_records import ChunkRecord

        captured["instructions"] = instructions
        captured["llm_category_ids"] = llm_category_ids
        return [], [
            ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=1,
                tokens_out=1,
                duration_ms=1,
            )
        ]

    monkeypatch.setattr(ap.AnthropicProvider, "detect_redactions", _spy_detect)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "redacted.pdf"
    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "--instruct",
            "also redact URLs",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert captured.get("instructions") == ("also redact URLs",)
    # pii rule set has llm categories (person_name, street_address)
    assert len(captured.get("llm_category_ids", ())) > 0


def test_run_prints_ocr_notice_when_scanned_pages_found(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic_client: dict[str, Any],
) -> None:
    from kuroi.core.pdf import ExtractionResult, Page

    pdf = make_pdf(["Hello world"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        "kuroi.cli.run.extract_word_index",
        lambda path: ExtractionResult(pages=(Page(number=1, words=()),), ocr_page_count=2),
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact all",
            "-y",
            "--in-place",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert "OCR applied to 2 scanned page(s)." in result.stdout


def test_run_exits_2_when_ocr_required_error(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic_client: dict[str, Any],
) -> None:
    from kuroi.core.pdf import OcrRequiredError

    pdf = make_pdf(["Hello world"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def _raise(path: Path) -> None:
        raise OcrRequiredError((3, 7))

    monkeypatch.setattr("kuroi.cli.run.extract_word_index", _raise)

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact all",
            "-y",
            "--in-place",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 2
    assert "3, 7" in result.stdout
    assert "tesseract" in result.stdout.lower()


def test_run_clean_error_when_backup_dir_uncreatable(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    """When --backup-dir can't be created, exit 2 with a path-naming message
    that points the user at --backup-dir."""
    dangling = tmp_path / "dangling-link"
    dangling.symlink_to(tmp_path / "missing-target")
    backup_dir = dangling / "backups"

    pdf = make_pdf(["x"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 2
    flat = "".join(result.stdout.split())
    assert "".join(str(backup_dir).split()) in flat
    assert "--backup-dir" in result.stdout
    assert not out.exists()


def test_run_no_backup_skips_backup_creation(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    """--no-backup writes the redacted output and audit log but creates no backup dir."""
    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "out.pdf"
    backup_dir = tmp_path / "backups"
    audit_dir = tmp_path / "audit"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--no-backup",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(audit_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    assert not backup_dir.exists()
    assert len(list(audit_dir.glob("*.jsonl"))) == 1
    assert "Backup:" not in result.stdout


def test_run_no_backup_with_in_place_warns_but_proceeds(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
) -> None:
    """--no-backup --in-place prints a yellow warning about unrecoverable original
    but proceeds with the redaction."""
    pdf = make_pdf(["Contact alice@example.com today"])

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "--in-place",
            "-y",
            "--no-backup",
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert pdf.exists()
    assert "unrecoverable" in result.stdout.lower()


def test_default_backup_dir_uses_xdg_data_home(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_anthropic_client: dict[str, Any],
) -> None:
    """Without --backup-dir, backups land under $XDG_DATA_HOME/kuroi/backups."""
    fake_xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_xdg))

    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--rules",
            "pii",
            "-o",
            str(out),
            "-y",
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    expected = fake_xdg / "kuroi" / "backups"
    assert expected.is_dir()
    assert any(p.is_dir() for p in expected.iterdir())


def test_run_with_pages_per_batch_invokes_orchestrator(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--pages-per-batch 2 on a 4-page document produces 2 LLM calls."""
    pdf = make_pdf(
        [
            "Page one alice@example.com",
            "Page two bob@example.com",
            "Page three carol@example.com",
            "Page four dave@example.com",
        ]
    )
    out = tmp_path / "redacted.pdf"
    backup_dir = tmp_path / "backups"
    audit_dir = tmp_path / "audit"

    call_page_groups: list[tuple[int, ...]] = []

    def _stub_detect(
        self: Any,
        pages: tuple[Any, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Any], list[Any]]:
        from kuroi.core.audit_records import ChunkRecord

        call_page_groups.append(tuple(p.number for p in pages))
        return [], [
            ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=seed,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=10,
                tokens_out=2,
                duration_ms=50,
            )
        ]

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.detect_redactions",
        _stub_detect,
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact emails",
            "-o",
            str(out),
            "-y",
            "--pages-per-batch",
            "2",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(audit_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert call_page_groups == [(1, 2), (3, 4)]


def test_run_with_pages_per_batch_prints_progress_per_batch(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = make_pdf(["one", "two", "three", "four"])

    def _stub_detect(
        self: Any,
        pages: tuple[Any, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Any], list[Any]]:
        from kuroi.core.audit_records import ChunkRecord

        return [], [
            ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=10,
                tokens_out=2,
                duration_ms=123,
            )
        ]

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.detect_redactions",
        _stub_detect,
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact",
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--pages-per-batch",
            "2",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Batch 1/2 (pages 1–2)" in result.stdout
    assert "Batch 2/2 (pages 3–4)" in result.stdout


def test_run_with_pages_per_batch_aborts_on_batch_error(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch that hard-fails twice exits 1 with a BatchError message."""
    pdf = make_pdf(["one", "two"])

    def _stub_detect(
        self: Any,
        pages: tuple[Any, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Any], list[Any]]:
        return [], []  # hard failure on every call

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.detect_redactions",
        _stub_detect,
    )
    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact",
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--pages-per-batch",
            "1",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 1
    from kuroi.core.config import DEFAULT_RETRY_POLICY

    expected_attempts = DEFAULT_RETRY_POLICY.max_retries + 1
    assert f"failed {expected_attempts} times" in result.stdout
    assert "smaller --pages-per-batch" in result.stdout


def test_run_max_retries_zero_disables_retry(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--max-retries 0` causes a single hard failure to abort with exit 1."""
    pdf = make_pdf(["one"])

    call_count = {"n": 0}

    def _stub_detect(
        self: Any,
        pages: tuple[Any, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Any], list[Any]]:
        call_count["n"] += 1
        return [], []  # hard failure

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.detect_redactions",
        _stub_detect,
    )
    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact",
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--max-retries",
            "0",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 1
    assert call_count["n"] == 1  # exactly one provider call, no retry
    assert "failed 1 times" in result.stdout


def test_run_default_path_uses_orchestrator_with_default_policy(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without --pages-per-batch, the run still routes through the orchestrator
    as a single batch covering the whole document, with the default retry policy."""
    pdf = make_pdf(["Page one", "Page two"])

    captured: dict[str, Any] = {}

    def _capture(*args: Any, **kwargs: Any) -> tuple[list[Any], list[Any]]:
        from kuroi.core.audit_records import ChunkRecord

        captured["args"] = args
        captured["kwargs"] = kwargs
        pages = args[1]
        return [], [
            ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=10,
                tokens_out=2,
                duration_ms=50,
            )
        ]

    monkeypatch.setattr("kuroi.cli.run.detect_redactions_chunked", _capture)

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact",
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    from kuroi.core.config import DEFAULT_RETRY_POLICY

    assert captured["kwargs"]["retry_policy"] == DEFAULT_RETRY_POLICY
    # Default path: one batch covering all pages — no per-batch progress UI
    assert captured["kwargs"]["on_batch_start"] is None
    assert captured["kwargs"]["on_batch_complete"] is None
    assert captured["kwargs"]["pages_per_batch"] == 2  # len(pages)
    assert "Batch 1/" not in result.stdout
