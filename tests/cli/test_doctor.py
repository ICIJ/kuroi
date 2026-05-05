import pytest
from typer.testing import CliRunner

from kuroi.cli import app


def test_doctor_runs_and_lists_known_checks(monkeypatch) -> None:
    # Ensure deterministic output regardless of host env
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1)
    out = result.stdout
    for label in ("kuroi version", "Python version", "Anthropic API key"):
        assert label in out


def test_doctor_reports_anthropic_key_set(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert "Anthropic API key" in result.stdout
    assert "set" in result.stdout


def test_doctor_reports_resolved_provider_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUROI_PROVIDER", "ollama")
    monkeypatch.setenv("KUROI_MODEL", "llama3.1:8b")
    monkeypatch.setenv("KUROI_OLLAMA_URL", "http://localhost:11434")

    # Stub the reachability probe to "reachable" so this test focuses on display.
    from kuroi.cli import doctor as doctor_module
    monkeypatch.setattr(doctor_module, "probe_ollama_models", lambda url: ["llama3.1:8b"])

    result = CliRunner().invoke(app, ["doctor"])
    assert "Provider" in result.stdout
    assert "ollama" in result.stdout
    assert "llama3.1:8b" in result.stdout


def test_doctor_reports_ollama_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUROI_PROVIDER", "ollama")
    monkeypatch.setenv("KUROI_MODEL", "llama3.1:8b")
    monkeypatch.setenv("KUROI_OLLAMA_URL", "http://localhost:11434")

    from kuroi.cli import doctor as doctor_module
    monkeypatch.setattr(doctor_module, "probe_ollama_models", lambda url: None)

    result = CliRunner().invoke(app, ["doctor"])
    assert "unreachable" in result.stdout.lower()
    # No traceback should leak into output
    assert "Traceback" not in result.stdout


def test_doctor_tesseract_detail_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None if name == "tesseract" else f"/usr/bin/{name}")
    result = CliRunner().invoke(app, ["doctor"])
    assert "required for scanned PDFs" in result.stdout
