"""Tests for the provider factory."""

from __future__ import annotations

import pytest

from kuroi.core.config import Config
from kuroi.providers.anthropic import AnthropicProvider
from kuroi.providers.factory import make_provider
from kuroi.providers.ollama import OllamaProvider


def test_make_provider_dispatches_to_anthropic() -> None:
    cfg = Config(provider="anthropic", model="claude-opus-4-7", ollama_url="http://localhost:11434")
    provider = make_provider(cfg)
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-opus-4-7"


def test_make_provider_dispatches_to_ollama() -> None:
    cfg = Config(provider="ollama", model="llama3.1:8b", ollama_url="http://localhost:11434")
    provider = make_provider(cfg)
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "llama3.1:8b"


def test_make_provider_defense_in_depth_unknown_provider() -> None:
    """resolve_config should already reject this; the factory is the second line."""
    bad = Config.__new__(Config)
    object.__setattr__(bad, "provider", "rogue")
    object.__setattr__(bad, "model", "x")
    object.__setattr__(bad, "ollama_url", "http://localhost:11434")
    with pytest.raises(ValueError):
        make_provider(bad)


def test_make_provider_dispatches_to_claude_cli() -> None:
    from kuroi.providers.claude_cli import ClaudeCliProvider

    cfg = Config(
        provider="claude-cli",
        model="claude-opus-4-7",
        ollama_url="http://localhost:11434",
        claude_cli_path="/opt/claude",
        claude_cli_timeout_s=600,
    )
    provider = make_provider(cfg)
    assert isinstance(provider, ClaudeCliProvider)
    assert provider.model == "claude-opus-4-7"
    assert provider._cli_path == "/opt/claude"
    assert provider._timeout_s == 600
