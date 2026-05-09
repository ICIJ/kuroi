"""Tests for ProviderRates cache multipliers."""

from kuroi.core.pricing import load_pricing


def test_anthropic_rates_have_default_cache_multipliers() -> None:
    pricing = load_pricing()
    rates = pricing.rates("anthropic", "claude-opus-4-7")
    # Anthropic ephemeral cache: writes cost 1.25x, reads cost 0.1x base input.
    assert rates.cache_write_multiplier == 1.25
    assert rates.cache_read_multiplier == 0.1


def test_ollama_rates_inherit_zero_multipliers() -> None:
    pricing = load_pricing()
    rates = pricing.rates("ollama", "any-model")
    # Ollama doesn't bill anything; multipliers are present and zero.
    assert rates.cache_write_multiplier == 0.0
    assert rates.cache_read_multiplier == 0.0
