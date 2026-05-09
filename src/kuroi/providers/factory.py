"""Construct a Provider from a resolved Config."""

from __future__ import annotations

from kuroi.core.config import Config
from kuroi.providers.anthropic import AnthropicProvider
from kuroi.providers.base import Provider
from kuroi.providers.claude_cli import ClaudeCliProvider
from kuroi.providers.ollama import OllamaProvider


def make_provider(config: Config) -> Provider:
    """Return the Provider instance described by `config`."""
    if config.provider == "anthropic":
        return AnthropicProvider(model=config.model)
    if config.provider == "ollama":
        return OllamaProvider(model=config.model, url=config.ollama_url)
    if config.provider == "claude-cli":
        return ClaudeCliProvider(
            model=config.model,
            cli_path=config.claude_cli_path,
            timeout_s=config.claude_cli_timeout_s,
        )
    raise ValueError(f"Unknown provider: {config.provider!r}")
