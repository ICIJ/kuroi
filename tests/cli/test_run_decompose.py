"""End-to-end CLI tests for instruction decomposition (project 2).

These exercise the full kuroi run path with a stub provider, asserting:
- Ollama runs with multi-rule --instruct fire N submissions per batch
  and write an instruction_decomposed audit event.
- Anthropic runs with the same instruction fire 1 submission per batch
  and write NO instruction_decomposed event.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from typer.testing import CliRunner

from kuroi.cli import app
from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding

runner = CliRunner()


def _make_finding(page: int = 1, start: int = 0, end: int = 0) -> Finding:
    return Finding(page=page, start=start, end=end, kind="test", confidence="high", source="stub")


class _FakeOllamaProvider:
    """Stub that mimics OllamaProvider's _client/_url/model surface AND
    its detect_redactions interface. Returns one finding so audit is opened."""

    name = "ollama"
    model = "llama3.1:8b"

    def __init__(self) -> None:
        from unittest.mock import MagicMock

        self._client = MagicMock()
        self._url = "http://localhost:11434"
        self.detect_calls: list[dict] = []
        self._call_idx = 0

    def detect_redactions(
        self,
        pages: Any,
        llm_category_ids: Any,
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
        model: str | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        self.detect_calls.append(
            {
                "model": model,
                "categories": llm_category_ids,
                "instructions": instructions,
            }
        )
        findings = [_make_finding()] if self._call_idx == 0 else []
        self._call_idx += 1
        chunk = ChunkRecord(
            chunk_idx=len(self.detect_calls) - 1,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=seed is not None,
            system_fingerprint=None,
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            tokens_in=10,
            tokens_out=2,
            duration_ms=50,
        )
        return findings, [chunk]


class _FakeAnthropicProvider:
    name = "anthropic"
    model = "claude-opus-4-7"

    def __init__(self) -> None:
        self.detect_calls: list[dict] = []

    def detect_redactions(
        self,
        pages: Any,
        llm_category_ids: Any,
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
        model: str | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        self.detect_calls.append({"instructions": instructions})
        return [_make_finding()], [
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


def _read_audit_events(audit_dir: Path) -> list[dict]:
    files = sorted(audit_dir.glob("*.jsonl"))
    assert files, f"No audit JSONL in {audit_dir}"
    events = []
    for line in files[-1].read_text().splitlines():
        events.append(json.loads(line))
    return events


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world", fontsize=11)
    p = tmp_path / "tiny.pdf"
    doc.save(str(p))
    doc.close()
    return p


@pytest.fixture
def _pass_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make verify_pdf always report passed so we don't need a real redaction."""
    from kuroi.core.verification import VerificationReport

    monkeypatch.setattr(
        "kuroi.cli.run.verify_pdf",
        lambda _path: VerificationReport(passed=True, leaks=()),
    )


def test_ollama_multirule_instruct_decomposes_and_dispatches_per_rule(
    tiny_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _pass_verification: None
) -> None:
    """5-numbered-rule --instruct on Ollama → 5 detect_redactions calls
    on the single-page batch + an instruction_decomposed audit event."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    fake = _FakeOllamaProvider()
    monkeypatch.setattr("kuroi.cli.run.make_provider", lambda cfg: fake)

    instruct = (
        "1. Redact all email addresses.\n"
        "2. Redact all phone numbers.\n"
        "3. Redact all street addresses.\n"
        "4. Redact all person names.\n"
        "5. Redact all IP addresses."
    )
    audit_dir = tmp_path / "audit"

    result = runner.invoke(
        app,
        [
            "run",
            str(tiny_pdf),
            "--instruct",
            instruct,
            "-o",
            str(tmp_path / "out.pdf"),
            "--overwrite",
            "-y",
            "--no-backup",
            "--audit-dir",
            str(audit_dir),
            "--provider",
            "ollama",
            "--model",
            "llama3.1:8b",
            "--ollama-url",
            "http://localhost:11434",
            "--pages-per-batch",
            "1",
            "--max-retries",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output

    # 1 page, 5 rules → 5 detect_redactions calls.
    assert len(fake.detect_calls) == 5
    instr_tuples = [c["instructions"] for c in fake.detect_calls]
    for it in instr_tuples:
        assert len(it) == 1
    flat = [it[0] for it in instr_tuples]
    assert any("email" in r for r in flat)
    assert any("phone" in r for r in flat)
    assert any("address" in r.lower() for r in flat)
    assert any("name" in r.lower() for r in flat)
    assert any("IP" in r for r in flat)

    events = _read_audit_events(audit_dir)
    decomp_events = [e for e in events if e.get("event") == "instruction_decomposed"]
    assert len(decomp_events) == 1
    e = decomp_events[0]
    assert e["source"] == "parser"
    assert e["rule_count"] == 5
    assert len(e["rules"]) == 5


def test_anthropic_multirule_instruct_does_not_decompose(
    tiny_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _pass_verification: None
) -> None:
    """Same multi-rule instruct on Anthropic → 1 call per batch, no
    decomposition audit event."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    fake = _FakeAnthropicProvider()
    monkeypatch.setattr("kuroi.cli.run.make_provider", lambda cfg: fake)

    instruct = "1. Redact emails.\n2. Redact phones.\n3. Redact addresses."
    audit_dir = tmp_path / "audit"

    result = runner.invoke(
        app,
        [
            "run",
            str(tiny_pdf),
            "--instruct",
            instruct,
            "-o",
            str(tmp_path / "out.pdf"),
            "--overwrite",
            "-y",
            "--no-backup",
            "--audit-dir",
            str(audit_dir),
            "--provider",
            "anthropic",
            "--model",
            "claude-opus-4-7",
            "--pages-per-batch",
            "1",
            "--max-retries",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output

    # Anthropic path: 1 call with the full instruction tuple (length 1, the original).
    assert len(fake.detect_calls) == 1
    assert fake.detect_calls[0]["instructions"] == (instruct,)

    events = _read_audit_events(audit_dir)
    decomp_events = [e for e in events if e.get("event") == "instruction_decomposed"]
    assert decomp_events == []


def test_run_ollama_no_instruct_does_not_decompose(
    tiny_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _pass_verification: None
) -> None:
    """Ollama run with no --instruct → no decomposer call, no audit event.
    The decomposer gate checks `provider.name == "ollama" and instruct`; when
    instruct is empty, decompose() is never called."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    fake = _FakeOllamaProvider()
    monkeypatch.setattr("kuroi.cli.run.make_provider", lambda cfg: fake)

    audit_dir = tmp_path / "audit"

    result = runner.invoke(
        app,
        [
            "run",
            str(tiny_pdf),
            "--rules",
            "pii-en",
            "-o",
            str(tmp_path / "out.pdf"),
            "--overwrite",
            "-y",
            "--no-backup",
            "--audit-dir",
            str(audit_dir),
            "--provider",
            "ollama",
            "--model",
            "llama3.1:8b",
            "--ollama-url",
            "http://localhost:11434",
            "--pages-per-batch",
            "1",
            "--max-retries",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output

    # With no --instruct, instruction tuple is empty even though --rules exists.
    assert len(fake.detect_calls) == 1
    assert fake.detect_calls[0]["instructions"] == ()
    # Decomposer was never invoked, so no HTTP call attempted.
    assert fake._client.post.call_count == 0

    events = _read_audit_events(audit_dir)
    decomp_events = [e for e in events if e.get("event") == "instruction_decomposed"]
    assert decomp_events == []
