"""Tests for the pricing module — load + lookup."""

import json
from pathlib import Path

import pytest

from kuroi.core.pricing import (
    OUTPUT_MULTIPLIER,
    Pricing,
    ProviderRates,
    count_tokens,
    estimate_cost,
    load_pricing,
)


def test_load_pricing_reads_packaged_file() -> None:
    pricing = load_pricing()
    assert isinstance(pricing, Pricing)
    assert "anthropic" in pricing.providers
    assert "claude-opus-4-7" in pricing.providers["anthropic"]


def test_load_pricing_lookup_returns_rates() -> None:
    pricing = load_pricing()
    rates = pricing.rates("anthropic", "claude-opus-4-7")
    assert isinstance(rates, ProviderRates)
    assert rates.input_per_million > 0
    assert rates.output_per_million > 0


def test_load_pricing_unknown_model_raises() -> None:
    pricing = load_pricing()
    with pytest.raises(KeyError):
        pricing.rates("anthropic", "model-that-does-not-exist")


def test_load_pricing_from_explicit_path(tmp_path: Path) -> None:
    body = {
        "schema_version": 1,
        "updated_at": "2026-04-15",
        "providers": {
            "anthropic": {
                "claude-opus-4-7": {"input_per_million": 15.0, "output_per_million": 75.0}
            }
        },
    }
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(body))
    pricing = load_pricing(p)
    rates = pricing.rates("anthropic", "claude-opus-4-7")
    assert rates.input_per_million == 15.0
    assert rates.output_per_million == 75.0


def test_count_tokens_short_text() -> None:
    # 12 chars / 4 = 3, plus the 500-token overhead = 503.
    assert count_tokens("hello world!") == 503


def test_count_tokens_empty_text_includes_overhead() -> None:
    assert count_tokens("") == 500


def test_count_tokens_long_text_scales() -> None:
    text = "a" * 4000
    # 4000 / 4 = 1000 tokens, plus 500 overhead = 1500.
    assert count_tokens(text) == 1500


def test_estimate_cost_anthropic_opus_one_thousand_tokens() -> None:
    pricing = load_pricing()
    # 1000 input tokens, 180 estimated output (0.18 x 1000).
    # Anthropic opus: 15.00/Mtok in, 75.00/Mtok out.
    # Cost = 1000/1e6 * 15 + 180/1e6 * 75 = 0.015 + 0.0135 = 0.0285
    cost = estimate_cost(pricing, "anthropic", "claude-opus-4-7", input_tokens=1000)
    assert cost == pytest.approx(0.0285, rel=1e-6)


def test_estimate_cost_ollama_is_zero() -> None:
    pricing = load_pricing()
    cost = estimate_cost(pricing, "ollama", "llama3.1:70b", input_tokens=10_000)
    assert cost == 0.0


def test_output_multiplier_constant() -> None:
    assert OUTPUT_MULTIPLIER == 0.18


def test_claude_cli_pricing_rates_are_zero() -> None:
    from kuroi.core.pricing import load_pricing

    pricing = load_pricing()
    rates = pricing.rates("claude-cli", "claude-opus-4-7")
    assert rates.input_per_million == 0.0
    assert rates.output_per_million == 0.0
    assert rates.cache_write_multiplier == 0.0
    assert rates.cache_read_multiplier == 0.0


def test_claude_cli_pricing_uses_wildcard_for_unknown_model() -> None:
    from kuroi.core.pricing import load_pricing

    pricing = load_pricing()
    rates = pricing.rates("claude-cli", "totally-made-up-model")
    assert rates.input_per_million == 0.0
    assert rates.output_per_million == 0.0
