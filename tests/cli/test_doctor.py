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
