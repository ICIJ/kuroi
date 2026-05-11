"""End-to-end: real PDF → run → verify → undo."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from typer.testing import CliRunner

from kuroi.cli import app


@pytest.fixture
def stub_provider(monkeypatch):
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

    def _fake_init(
        self,
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

    monkeypatch.setattr("kuroi.providers.anthropic.AnthropicProvider.__init__", _fake_init)


def test_full_run_then_verify_then_undo(
    make_pdf: Callable[..., Path], tmp_path: Path, stub_provider
) -> None:
    pdf = make_pdf(
        [
            "Subject: meeting prep",
            "Please email alice@example.com about the SSN 123-45-6789 issue.",
        ],
        filename="memo.pdf",
    )
    pre_redaction_bytes = pdf.read_bytes()

    backup_dir = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    out = tmp_path / "memo.redacted.pdf"

    runner = CliRunner()

    # 1. run
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
            str(audit_dir),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    text = pymupdf.open(str(out))[0].get_text("text")
    assert "alice@example.com" not in text
    assert "123-45-6789" not in text

    # 2. verify
    result = runner.invoke(app, ["verify", str(out)])
    assert result.exit_code == 0

    # 3. mutate the original then undo
    pdf.write_bytes(b"%PDF-1.4\n% mutated\n")
    result = runner.invoke(app, ["undo", "-y", "--backup-dir", str(backup_dir)])
    assert result.exit_code == 0
    assert pdf.read_bytes() == pre_redaction_bytes


@pytest.fixture
def stub_ollama_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch httpx.Client.post within OllamaProvider to return empty findings."""
    import json as _json

    class _R:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {
                "message": {"role": "assistant", "content": _json.dumps({"findings": []})},
                "done": True,
            }

    class _C:
        def post(self, url: str, *, json: dict[str, Any], timeout: Any = None) -> _R:
            return _R()

    def _fake_init(
        self: Any,
        *,
        model: str,
        url: str,
        client: Any | None = None,
    ) -> None:
        self.name = "ollama"
        self.model = model
        self._url = url.rstrip("/")
        self._client = _C()

    monkeypatch.setattr("kuroi.providers.ollama.OllamaProvider.__init__", _fake_init)


def test_full_run_then_verify_then_undo_ollama(
    make_pdf: Callable[..., Path], tmp_path: Path, stub_ollama_client: None
) -> None:
    pdf = make_pdf(
        [
            "Subject: meeting prep",
            "Please email alice@example.com about the SSN 123-45-6789 issue.",
        ],
        filename="memo.pdf",
    )
    pre_redaction_bytes = pdf.read_bytes()

    backup_dir = tmp_path / "backups"
    audit_dir = tmp_path / "audit"
    out = tmp_path / "memo.redacted.pdf"

    runner = CliRunner()

    # 1. run with --provider ollama --model llama3.1:8b
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
            str(audit_dir),
            "--provider",
            "ollama",
            "--model",
            "llama3.1:8b",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out.is_file()
    text = pymupdf.open(str(out))[0].get_text("text")
    assert "alice@example.com" not in text
    assert "123-45-6789" not in text

    # 2. verify
    result = runner.invoke(app, ["verify", str(out)])
    assert result.exit_code == 0

    # 3. undo
    pdf.write_bytes(b"%PDF-1.4\n% mutated\n")
    result = runner.invoke(app, ["undo", "-y", "--backup-dir", str(backup_dir)])
    assert result.exit_code == 0
    assert pdf.read_bytes() == pre_redaction_bytes
