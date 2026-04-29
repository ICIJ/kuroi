"""Pricing data + cost estimation.

Pricing entries ship with the package at `kuroi/data/pricing.json`. The file
is loaded once via `load_pricing()`. Per-provider/model rates are looked up
through `Pricing.rates()`. Token counts use a static character-based heuristic
(see `count_tokens`); costs are computed by `estimate_cost`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class ProviderRates:
    """Per-million-token USD rates for a single (provider, model)."""

    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class Pricing:
    """The full pricing table loaded from disk."""

    schema_version: int
    updated_at: str
    providers: dict[str, dict[str, ProviderRates]]

    def rates(self, provider: str, model: str) -> ProviderRates:
        """Look up rates. Falls back to the wildcard `"*"` entry if present."""
        provider_table = self.providers[provider]
        if model in provider_table:
            return provider_table[model]
        if "*" in provider_table:
            return provider_table["*"]
        raise KeyError(f"No pricing for {provider}/{model}")


def load_pricing(path: Path | None = None) -> Pricing:
    """Load pricing from `path` (for tests) or the packaged default."""
    if path is None:
        text = (files("kuroi") / "data" / "pricing.json").read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    providers: dict[str, dict[str, ProviderRates]] = {}
    for provider_name, models in raw["providers"].items():
        providers[provider_name] = {
            model_name: ProviderRates(
                input_per_million=float(rates["input_per_million"]),
                output_per_million=float(rates["output_per_million"]),
            )
            for model_name, rates in models.items()
        }
    return Pricing(
        schema_version=int(raw["schema_version"]),
        updated_at=str(raw["updated_at"]),
        providers=providers,
    )


PROMPT_OVERHEAD_TOKENS = 500
"""Static budget for system prompt + JSON schema + output schema hints.

This is a calibration parameter. The post-run divergence note logs cases where
the actual / estimated cost ratio exceeds 2.0 so this value can be tuned.
"""


def count_tokens(text: str) -> int:
    """Heuristic token count.

    Uses 4 chars/token (typical for English-leaning content across the major
    BPE tokenizers). Adds `PROMPT_OVERHEAD_TOKENS` for the kuroi-side prompt.
    """
    return len(text) // 4 + PROMPT_OVERHEAD_TOKENS


OUTPUT_MULTIPLIER = 0.18
"""Static estimate that output tokens = input tokens × 0.18.

The JSON response is dominated by `(page, start, end)` integers and short
kind labels, empirically much smaller than the input. Frozen at v1.0;
calibration data goes through the post-run divergence note in cli/run.py.
"""


def estimate_cost(
    pricing: Pricing,
    provider: str,
    model: str,
    *,
    input_tokens: int,
) -> float:
    """Pre-flight cost estimate in USD."""
    rates = pricing.rates(provider, model)
    output_tokens = input_tokens * OUTPUT_MULTIPLIER
    return (
        input_tokens / 1_000_000 * rates.input_per_million
        + output_tokens / 1_000_000 * rates.output_per_million
    )
