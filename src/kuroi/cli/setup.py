"""kuroi setup — interactive configuration writer."""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
import typer
from rich.console import Console

from kuroi.core.config import (
    DEFAULT_OLLAMA_URL,
    VALID_PROVIDERS,
    Config,
    ConfigError,
    ProviderName,
    load_config_file,
    write_config_file,
    xdg_config_home,
)

console = Console()

CURATED_ANTHROPIC_MODELS: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)

PROBE_TIMEOUT_SECONDS = 3.0


def probe_ollama_models(url: str) -> list[str] | None:
    """GET /api/tags. Returns model names on success, None if unreachable.

    Any HTTP error, timeout, or malformed response is treated as "unreachable"
    so the caller can fall back to free-text model entry.
    """
    try:
        response = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=PROBE_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.append(entry["name"])
    return names


def probe_claude_cli(cli_path: str | None = None) -> bool:
    """Verify the Claude CLI is reachable and authenticated. True on success.

    Runs a single one-shot `query()` with a tiny prompt and asserts a
    response message arrives. Returns False on any SDK error.
    """
    import anyio

    async def _ping() -> bool:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                query as sdk_query,
            )
        except ImportError:
            return False
        options = ClaudeAgentOptions(
            system_prompt="Reply with exactly: pong",
            max_turns=1,
            allowed_tools=[],
            permission_mode="default",
            setting_sources=[],
            cli_path=cli_path,
        )
        try:
            async for _ in sdk_query(prompt="ping", options=options):
                return True
        except Exception:
            return False
        return False

    try:
        return bool(anyio.run(_ping))
    except Exception:
        return False


def _default_index(default: str | None, options: tuple[str, ...] | list[str]) -> str:
    """The numeric default for a numbered picker; falls back to '1' if `default` isn't listed."""
    if default is not None and default in options:
        return str(list(options).index(default) + 1)
    return "1"


def _prompt_provider(default: str) -> str:
    console.print("[bold]Which LLM provider?[/]")
    console.print("  1. anthropic")
    console.print("  2. ollama")
    console.print("  3. claude-cli (uses your Claude Code subscription)")
    if default == "anthropic":
        default_index = "1"
    elif default == "ollama":
        default_index = "2"
    else:
        default_index = "3"
    choice = typer.prompt("Enter choice [1/2/3]", default=default_index)
    if choice.strip() in ("1", "anthropic"):
        return "anthropic"
    if choice.strip() in ("2", "ollama"):
        return "ollama"
    if choice.strip() in ("3", "claude-cli"):
        return "claude-cli"
    console.print(f"[yellow]Unrecognized choice {choice!r}; keeping {default}.[/]")
    return default


def _prompt_anthropic_model(default: str) -> str:
    console.print("[bold]Pick an Anthropic model:[/]")
    for idx, name in enumerate(CURATED_ANTHROPIC_MODELS, start=1):
        marker = "  *" if name == default else "   "
        console.print(f"{marker} {idx}. {name}")
    console.print(f"   {len(CURATED_ANTHROPIC_MODELS) + 1}. (other — type a model ID)")
    raw = typer.prompt(
        "Enter choice", default=_default_index(default, CURATED_ANTHROPIC_MODELS)
    ).strip()
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(CURATED_ANTHROPIC_MODELS):
            return CURATED_ANTHROPIC_MODELS[n - 1]
        if n == len(CURATED_ANTHROPIC_MODELS) + 1:
            return str(typer.prompt("Model ID"))
    # Treat anything else as a typed model ID.
    return raw or default


def _prompt_ollama_model(default: str | None, available: list[str] | None) -> str:
    if available:
        console.print("[bold]Pick an Ollama model (installed on the daemon):[/]")
        for idx, name in enumerate(available, start=1):
            marker = "  *" if name == default else "   "
            console.print(f"{marker} {idx}. {name}")
        console.print(f"   {len(available) + 1}. (other — type a model name)")
        raw = typer.prompt("Enter choice", default=_default_index(default, available)).strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(available):
                return available[n - 1]
            if n == len(available) + 1:
                return str(typer.prompt("Model name")).strip()
        return raw or (default or "")
    # Free-text fallback
    return str(typer.prompt("Model name", default=default or "")).strip()


def setup() -> None:
    """Interactively configure provider, model, and Ollama URL."""
    cfg_path = xdg_config_home() / "kuroi" / "config.toml"
    try:
        existing = load_config_file(cfg_path)
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=2) from exc
    _raw_provider = existing.get("provider")
    cur_provider: str = _raw_provider if isinstance(_raw_provider, str) else "anthropic"
    _raw_model = existing.get("model")
    cur_model: str | None = _raw_model if isinstance(_raw_model, str) else None
    _raw_ollama = existing.get("ollama")
    cur_ollama: dict[str, Any] = _raw_ollama if isinstance(_raw_ollama, dict) else {}
    _raw_url = cur_ollama.get("url")
    cur_ollama_url: str = _raw_url if isinstance(_raw_url, str) else DEFAULT_OLLAMA_URL

    provider_str = _prompt_provider(cur_provider or "anthropic")

    if provider_str == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            console.print(
                "[yellow]warning:[/] ANTHROPIC_API_KEY is not set. "
                "Cloud redaction will fail until you export it."
            )
        default_model = (
            cur_model if (cur_model in CURATED_ANTHROPIC_MODELS) else CURATED_ANTHROPIC_MODELS[0]
        )
        model = _prompt_anthropic_model(default_model)
        ollama_url: str = cur_ollama_url
    elif provider_str == "claude-cli":
        if not probe_claude_cli():
            console.print(
                "[red]Claude CLI is not reachable.[/] "
                "Install with `pip install claude-agent-sdk` or "
                "`npm install -g @anthropic-ai/claude-code`, then run "
                "`claude /login` to authenticate. Re-run `kuroi setup` "
                "when ready."
            )
            raise typer.Exit(code=2)
        default_model = (
            cur_model
            if cur_model in CURATED_ANTHROPIC_MODELS
            else CURATED_ANTHROPIC_MODELS[0]
        )
        model = _prompt_anthropic_model(default_model)
        ollama_url = cur_ollama_url
    else:
        url_default: str = cur_ollama_url
        url = str(typer.prompt("Ollama base URL", default=url_default)).strip() or url_default
        available = probe_ollama_models(url)
        if available is None:
            console.print(
                f"[yellow]warning:[/] {url} is unreachable. "
                "You can still configure a model name manually."
            )
        model = _prompt_ollama_model(cur_model, available)
        if not model:
            console.print("[red]No model entered; aborting.[/]")
            raise typer.Exit(code=2)
        ollama_url = url

    if provider_str not in VALID_PROVIDERS:
        provider_str = "anthropic"
    provider = cast("ProviderName", provider_str)
    config = Config(provider=provider, model=model, ollama_url=ollama_url)
    try:
        write_config_file(cfg_path, config)
    except OSError as exc:
        console.print(f"[red]Could not write config to {cfg_path}:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"  Wrote {cfg_path}")
