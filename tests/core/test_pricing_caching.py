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


def test_compute_actual_cost_factors_cache_multipliers() -> None:
    """The actual-cost computation in cli/run.py uses cache_write_multiplier
    (1.25x) and cache_read_multiplier (0.1x). With 1180 written tokens
    and 64900 read tokens at $15/MTok input, expected savings are:
      regular: 235200 (the document portion across batches) * 15/M = $3.528
      write:    1180 * 15/M * 1.25 = $0.022
      read:    64900 * 15/M * 0.1  = $0.097
      output:  22400 * 75/M        = $1.680
      total:                          $5.327
    """
    from kuroi.cli.run import _compute_actual_cost
    from kuroi.core.audit_records import ChunkRecord
    from kuroi.core.pricing import load_pricing

    pricing = load_pricing()

    chunks = [
        ChunkRecord(
            chunk_idx=i,
            pages=(i + 1,),
            temperature=0.0,
            seed_requested=None,
            seed_honored=False,
            system_fingerprint=None,
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            tokens_in=4200,  # uncached input portion (the document) per batch
            tokens_out=400,
            duration_ms=5000,
            cache_creation_input_tokens=1180 if i == 0 else 0,
            cache_read_input_tokens=0 if i == 0 else 1180,
        )
        for i in range(56)
    ]

    cost = _compute_actual_cost(chunks, pricing, "anthropic", "claude-opus-4-7")

    # Reference per the worked example in the spec.
    assert abs(cost - 5.327) < 0.01
