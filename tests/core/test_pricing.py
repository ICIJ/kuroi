"""Tests for the pricing module — load + lookup."""

import json
from pathlib import Path

import pytest

from kuroi.core.pricing import Pricing, ProviderRates, load_pricing


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


from kuroi.core.pricing import count_tokens


def test_count_tokens_short_text() -> None:
    # 12 chars / 4 = 3, plus the 500-token overhead = 503.
    assert count_tokens("hello world!") == 503


def test_count_tokens_empty_text_includes_overhead() -> None:
    assert count_tokens("") == 500


def test_count_tokens_long_text_scales() -> None:
    text = "a" * 4000
    # 4000 / 4 = 1000 tokens, plus 500 overhead = 1500.
    assert count_tokens(text) == 1500
