from typer.testing import CliRunner

from kuroi.cli import app

runner = CliRunner()


def test_models_default_lists_anthropic_table():
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "Anthropic" in result.stdout
    assert "claude-opus-4-7" in result.stdout
    assert "$" in result.stdout
    assert "cloud" in result.stdout


def test_models_filter_by_provider_anthropic():
    result = runner.invoke(app, ["models", "anthropic"])
    assert result.exit_code == 0
    assert "Anthropic" in result.stdout
    assert "Ollama" not in result.stdout


def test_models_filter_unknown_provider():
    result = runner.invoke(app, ["models", "no-such-provider"])
    assert result.exit_code == 2
