"""Tests for per-category model routing in the chunker."""

from kuroi.core.chunking import _partition_categories_by_model
from kuroi.core.rules import Category


def _cat(id_: str, model: str | None = None) -> Category:
    return Category(
        id=id_,
        label=id_,
        detection="llm",
        confidence="medium",
        pattern=None,
        model=model,
    )


def test_partition_groups_categories_with_same_model() -> None:
    cats = (
        _cat("a", model="claude-haiku-4-5"),
        _cat("b", model="claude-haiku-4-5"),
        _cat("c", model="claude-sonnet-4-6"),
    )
    groups = _partition_categories_by_model(cats, default_model="claude-opus-4-7")
    assert groups == {
        "claude-haiku-4-5": ("a", "b"),
        "claude-sonnet-4-6": ("c",),
    }


def test_partition_assigns_default_model_when_category_model_is_none() -> None:
    cats = (_cat("a", model=None), _cat("b", model="claude-haiku-4-5"))
    groups = _partition_categories_by_model(cats, default_model="claude-opus-4-7")
    assert groups == {
        "claude-opus-4-7": ("a",),
        "claude-haiku-4-5": ("b",),
    }


def test_partition_collapses_to_one_group_when_all_default() -> None:
    cats = (_cat("a"), _cat("b"), _cat("c"))
    groups = _partition_categories_by_model(cats, default_model="claude-opus-4-7")
    assert groups == {"claude-opus-4-7": ("a", "b", "c")}


def test_partition_skips_non_llm_categories() -> None:
    """Regex categories never go to the LLM; they shouldn't appear in any group."""
    cats = (
        _cat("a"),
        Category(id="email", label="email", detection="regex", confidence="high", pattern="x"),
    )
    groups = _partition_categories_by_model(cats, default_model="claude-opus-4-7")
    assert groups == {"claude-opus-4-7": ("a",)}


def test_partition_returns_empty_dict_for_no_llm_categories() -> None:
    groups = _partition_categories_by_model((), default_model="claude-opus-4-7")
    assert groups == {}


def test_batch_summary_aggregates_chunks() -> None:
    from kuroi.core.audit_records import ChunkRecord
    from kuroi.core.chunking import BatchSummary

    c1 = ChunkRecord(
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
        cache_creation_input_tokens=200,
        cache_read_input_tokens=0,
    )
    c2 = ChunkRecord(
        chunk_idx=1,
        pages=(1,),
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="c" * 64,
        response_sha256="d" * 64,
        tokens_in=80,
        tokens_out=15,
        duration_ms=300,  # faster — should NOT be the reported duration
        cache_creation_input_tokens=0,
        cache_read_input_tokens=200,
    )

    summary = BatchSummary.from_chunks(
        batch_idx=0, total_batches=1, page_numbers=(1,), chunks=(c1, c2)
    )

    assert summary.duration_ms == 500  # max, not sum, because groups run concurrently
    assert summary.tokens_in == 180
    assert summary.tokens_out == 35
    assert summary.cache_creation_input_tokens == 200
    assert summary.cache_read_input_tokens == 200
    assert summary.chunks == (c1, c2)
