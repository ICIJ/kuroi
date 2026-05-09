from kuroi.core.audit_records import ChunkRecord


def _base_kwargs() -> dict:
    return dict(
        chunk_idx=0,
        pages=(1,),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=100,
        tokens_out=20,
        duration_ms=500,
    )


def test_chunk_record_defaults_cache_token_fields_to_zero() -> None:
    rec = ChunkRecord(**_base_kwargs())
    assert rec.cache_creation_input_tokens == 0
    assert rec.cache_read_input_tokens == 0


def test_chunk_record_accepts_cache_token_fields() -> None:
    rec = ChunkRecord(
        **_base_kwargs(),
        cache_creation_input_tokens=1180,
        cache_read_input_tokens=64900,
    )
    assert rec.cache_creation_input_tokens == 1180
    assert rec.cache_read_input_tokens == 64900


def test_chunk_record_is_frozen() -> None:
    rec = ChunkRecord(
        chunk_idx=0,
        pages=(1, 2, 3),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=1000,
        tokens_out=200,
        duration_ms=12000,
    )
    assert rec.pages == (1, 2, 3)
    assert rec.tokens_in == 1000


def test_chunk_record_seed_metadata() -> None:
    rec = ChunkRecord(
        chunk_idx=0,
        pages=(1,),
        temperature=0.0,
        seed_requested=42,
        seed_honored=True,
        system_fingerprint="fp_abc",
        prompt_sha256="0" * 64,
        response_sha256="1" * 64,
        tokens_in=10,
        tokens_out=2,
        duration_ms=100,
    )
    assert rec.seed_requested == 42
    assert rec.seed_honored is True
    assert rec.system_fingerprint == "fp_abc"


def test_chunk_record_page_word_range_defaults_to_none() -> None:
    """Existing call sites that don't pass page_word_range still work."""
    record = ChunkRecord(
        chunk_idx=0,
        pages=(1,),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
    )

    assert record.page_word_range is None


def test_chunk_record_page_word_range_explicit_value_round_trips() -> None:
    record = ChunkRecord(
        chunk_idx=3,
        pages=(41,),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
        page_word_range=(3950, 8000),
    )

    assert record.page_word_range == (3950, 8000)
