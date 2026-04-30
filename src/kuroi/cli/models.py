"""kuroi models — list available LLM providers and their models."""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import typer
from rich.console import Console

from kuroi.core.config import (
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
)
from kuroi.core.pricing import load_pricing

models_app = typer.Typer()
console = Console()


PRIVACY_POSTURE = {"anthropic": "cloud", "openai": "cloud", "ollama": "local"}
SEED_SUPPORT = {
    "anthropic": "temperature=0 only (best-effort, recorded in audit)",
    "openai": "full (system_fingerprint-bounded)",
    "ollama": "full",
}


def models(
    provider: str | None = typer.Argument(None, help="Filter to one provider."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List available LLM providers and their models."""
    pricing = load_pricing()
    if provider is not None and provider not in pricing.providers:
        console.print(f"[red]unknown provider:[/] {provider}")
        raise typer.Exit(code=2)

    selected = {provider: pricing.providers[provider]} if provider else pricing.providers

    config = _resolve_config_or_default()

    if json_out:
        typer.echo(_json.dumps(_to_json(pricing, selected, config), indent=2))
        return

    for prov_name, prov_models in selected.items():
        posture = PRIVACY_POSTURE.get(prov_name, "?")
        console.print(
            f"\n[bold]{prov_name.title()}[/]                                                   {posture}"
        )
        installed: set[str] = set()
        if prov_name == "ollama":
            installed = _ollama_installed_models(config.ollama_url)
        for model_name, rates in prov_models.items():
            if model_name == "*":
                continue
            default_marker = (
                "(default)" if (config.provider == prov_name and config.model == model_name) else ""
            )
            install_marker = "(installed)" if model_name in installed else ""
            cost = (
                "free"
                if rates.input_per_million == 0.0 and rates.output_per_million == 0.0
                else f"${rates.input_per_million:.2f} / ${rates.output_per_million:.2f} per Mtok"
            )
            console.print(f"  {model_name:<22} {default_marker or install_marker:<11} {cost}")
        console.print(f"  seed support: {SEED_SUPPORT.get(prov_name, 'unknown')}")

    console.print(f"\nDefault: {config.provider} / {config.model}   (configurable)")
    console.print(f"Pricing last updated: {pricing.updated_at}")


def _resolve_config_or_default() -> Any:
    try:
        return resolve_config(
            ConfigOverrides(),
            env={},
            file_path=xdg_config_home() / "kuroi" / "config.toml",
        )
    except Exception:
        # Tolerate config errors here — `kuroi models` is informational.
        from types import SimpleNamespace

        return SimpleNamespace(
            provider="anthropic",
            model="claude-opus-4-7",
            ollama_url="http://localhost:11434",
        )


def _ollama_installed_models(url: str) -> set[str]:
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=2.0)
        r.raise_for_status()
        data = r.json()
        return {entry["name"] for entry in data.get("models", [])}
    except (httpx.HTTPError, KeyError, ValueError):
        return set()


def _to_json(pricing: Any, selected: dict[str, Any], config: Any) -> dict[str, Any]:
    return {
        "pricing_updated_at": pricing.updated_at,
        "default_provider": config.provider,
        "default_model": config.model,
        "providers": {
            prov_name: {
                "privacy": PRIVACY_POSTURE.get(prov_name, "unknown"),
                "seed_support": SEED_SUPPORT.get(prov_name, "unknown"),
                "models": {
                    m_name: {
                        "input_per_million": r.input_per_million,
                        "output_per_million": r.output_per_million,
                    }
                    for m_name, r in prov_models.items()
                    if m_name != "*"
                },
            }
            for prov_name, prov_models in selected.items()
        },
    }
