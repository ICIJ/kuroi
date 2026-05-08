"""Verify CLI flags are threaded through resolve_config + make_provider correctly."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from kuroi.cli import app
from kuroi.core.config import Config
from kuroi.core.findings import Finding


class _StubProvider:
    name = "stub"

    def __init__(self, model: str) -> None:
        self.model = model

    def detect_redactions(
        self,
        pages: Any,
        llm_category_ids: Any,
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
    ) -> tuple[list[Finding], list[Any]]:
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
                tokens_in=1,
                tokens_out=1,
                duration_ms=1,
            )
        ]


def _common_args(pdf: Path, out: Path, tmp_path: Path) -> list[str]:
    return [
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
    ]


def test_run_default_uses_built_in_anthropic(
    make_pdf: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Config] = {}

    def _fake_make(cfg: Config) -> _StubProvider:
        captured["config"] = cfg
        return _StubProvider(model=cfg.model)

    monkeypatch.setattr("kuroi.cli.run.make_provider", _fake_make)

    pdf = make_pdf(["alice@example.com"])
    out = tmp_path / "out.pdf"

    result = CliRunner().invoke(app, _common_args(pdf, out, tmp_path))
    assert result.exit_code == 0, result.stdout
    cfg = captured["config"]
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-opus-4-7"


def test_run_cli_flags_reach_resolve_config(
    make_pdf: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Config] = {}

    def _fake_make(cfg: Config) -> _StubProvider:
        captured["config"] = cfg
        return _StubProvider(model=cfg.model)

    monkeypatch.setattr("kuroi.cli.run.make_provider", _fake_make)

    pdf = make_pdf(["alice@example.com"])
    out = tmp_path / "out.pdf"

    result = CliRunner().invoke(
        app,
        [
            *_common_args(pdf, out, tmp_path),
            "--provider", "ollama",
            "--model", "llama3.1:8b",
            "--ollama-url", "http://example:11434",
        ],
    )
    assert result.exit_code == 0, result.stdout
    cfg = captured["config"]
    assert cfg.provider == "ollama"
    assert cfg.model == "llama3.1:8b"
    assert cfg.ollama_url == "http://example:11434"


def test_run_reports_config_error_with_exit_code_2(
    make_pdf: Callable[..., Path], tmp_path: Path
) -> None:
    pdf = make_pdf(["alice@example.com"])
    out = tmp_path / "out.pdf"

    # Ollama with no model → ConfigError (Anthropic has a default; Ollama doesn't)
    result = CliRunner().invoke(
        app,
        [*_common_args(pdf, out, tmp_path), "--provider", "ollama"],
    )
    assert result.exit_code == 2
    assert "Ollama" in result.stdout or "model" in result.stdout.lower()
