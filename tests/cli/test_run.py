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

    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    redacted = pymupdf.open(str(out))
    text = redacted[0].get_text("text")
    redacted.close()
    assert "alice@example.com" not in text
    assert "Contact" in text and "today" in text


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


def test_run_seed_flag_anthropic_prints_not_honored_notice(
    monkeypatch, make_pdf, tmp_path
):
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
        lambda **kw: real_provider(
            client=_StubClient(), model=kw.get("model", "claude-opus-4-7")
        ),
    )

    pdf = make_pdf(["dummy text"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
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
        lambda **kw: real_provider(
            client=_StubClient(), model=kw.get("model", "claude-opus-4-7")
        ),
    )

    pdf = make_pdf(["short"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
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

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.__init__", _fake_init
    )

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
        _json2.loads(line)
        for line in lines
        if _json2.loads(line).get("event") == "chunk_request"
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
    old_name = (datetime.now(UTC) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H-%M-%SZ-deadbe"
    )
    (backup_dir / old_name).mkdir()
    (backup_dir / old_name / "manifest.json").write_text("{}")

    pdf = make_pdf(["Contact alice@example.com today"])
    out = tmp_path / "out.pdf"

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
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
