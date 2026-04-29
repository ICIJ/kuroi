from kuroi.core.audit_records import ChunkRecord


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
