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


def test_uniform_default_model_dispatches_one_call_per_batch() -> None:
    """When all categories use the default model, behavior is identical to
    today's single-call-per-batch shape."""
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import DEFAULT_RETRY_POLICY
    from kuroi.core.findings import Finding
    from kuroi.core.pdf import Page, Word
    from kuroi.core.audit_records import ChunkRecord

    class _Recorder:
        name = "stub"
        model = "claude-opus-4-7"

        def __init__(self):
            self.calls: list[tuple[tuple[str, ...], str | None]] = []

        def detect_redactions(self, pages, llm_category_ids, *, instructions=(),
                              seed=None, attempt=0, layout_aware=False, model=None):
            self.calls.append((llm_category_ids, model))
            return [], [_chunk_for(pages)]

    def _chunk_for(pages):
        return ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=None,
            seed_honored=False,
            system_fingerprint=None,
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            tokens_in=10,
            tokens_out=2,
            duration_ms=100,
        )

    pages = (Page(number=1, words=(Word(idx=0, text="x", bbox=(0, 0, 1, 1)),)),)
    provider = _Recorder()

    detect_redactions_chunked(
        provider,
        pages,
        ("a", "b"),
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
    )

    # All categories on the default → exactly one call.
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == ("a", "b")


def test_mixed_models_dispatch_per_group() -> None:
    """Categories declared on different models produce one provider call
    per non-empty model group, per page-batch."""
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import DEFAULT_RETRY_POLICY
    from kuroi.core.audit_records import ChunkRecord
    from kuroi.core.pdf import Page, Word
    from kuroi.core.rules import Category

    class _Recorder:
        name = "stub"
        model = "claude-opus-4-7"

        def __init__(self):
            self.calls: list[tuple[tuple[str, ...], str | None]] = []

        def detect_redactions(self, pages, llm_category_ids, *, instructions=(),
                              seed=None, attempt=0, layout_aware=False, model=None):
            self.calls.append((llm_category_ids, model))
            return [], [ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=1,
                tokens_out=1,
                duration_ms=1,
            )]

    pages = (Page(number=1, words=(Word(idx=0, text="x", bbox=(0, 0, 1, 1)),)),)
    cats = (
        Category("a", "a", "llm", "medium", None, model="claude-haiku-4-5"),
        Category("b", "b", "llm", "medium", None, model=None),  # default
        Category("c", "c", "llm", "medium", None, model="claude-haiku-4-5"),
    )
    provider = _Recorder()

    detect_redactions_chunked(
        provider,
        pages,
        ("a", "b", "c"),
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
        categories=cats,
    )

    # Two groups: {haiku: (a, c), default: (b,)}. Two calls per the single batch.
    assert len(provider.calls) == 2
    by_model = {model: cat_ids for cat_ids, model in provider.calls}
    assert by_model["claude-haiku-4-5"] == ("a", "c")
    assert by_model["claude-opus-4-7"] == ("b",)


def test_instructions_route_to_default_model_in_their_own_call() -> None:
    """Instructions get dispatched on the default model with empty categories,
    one call per batch in addition to any category-group calls."""
    from kuroi.core.chunking import detect_redactions_chunked
    from kuroi.core.config import DEFAULT_RETRY_POLICY
    from kuroi.core.audit_records import ChunkRecord
    from kuroi.core.pdf import Page, Word
    from kuroi.core.rules import Category

    class _Recorder:
        name = "stub"
        model = "claude-opus-4-7"

        def __init__(self):
            self.calls: list[tuple[tuple[str, ...], tuple[str, ...], str | None]] = []

        def detect_redactions(self, pages, llm_category_ids, *, instructions=(),
                              seed=None, attempt=0, layout_aware=False, model=None):
            self.calls.append((llm_category_ids, instructions, model))
            return [], [ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=None,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=1,
                tokens_out=1,
                duration_ms=1,
            )]

    pages = (Page(number=1, words=(Word(idx=0, text="x", bbox=(0, 0, 1, 1)),)),)
    cats = (Category("a", "a", "llm", "medium", None, model="claude-haiku-4-5"),)
    provider = _Recorder()

    detect_redactions_chunked(
        provider,
        pages,
        ("a",),
        instructions=("redact URLs",),
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
        categories=cats,
    )

    # Two calls: one for the haiku-bound category, one for the instructions
    # against the default model.
    assert len(provider.calls) == 2
    instruction_calls = [c for c in provider.calls if c[1] == ("redact URLs",)]
    assert len(instruction_calls) == 1
    assert instruction_calls[0][0] == ()  # no categories on the instruction call
    assert instruction_calls[0][2] == "claude-opus-4-7"
