import json as _json

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


def test_models_json_output_is_parseable():
    result = runner.invoke(app, ["models", "--json"])
    assert result.exit_code == 0
    data = _json.loads(result.stdout)
    assert "providers" in data
    assert "anthropic" in data["providers"]
    assert "models" in data["providers"]["anthropic"]


def test_models_lists_claude_cli_with_subscription_label() -> None:
    from typer.testing import CliRunner

    from kuroi.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    out = result.stdout.lower()
    assert "claude-cli" in out
    assert "subscription" in out
