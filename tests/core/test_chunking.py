"""Tests for the per-batch chunking orchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.config import DEFAULT_RETRY_POLICY, RetryPolicy
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, Word


def _page(num: int) -> Page:
    return Page(
        number=num,
        words=(Word(idx=0, text=f"w{num}", bbox=(0.0, 0.0, 1.0, 1.0)),),
    )


def _finding(page: int) -> Finding:
    return Finding(page=page, start=0, end=0, kind="x", confidence="high", source="llm")


def _chunk(pages: tuple[int, ...]) -> ChunkRecord:
    return ChunkRecord(
        chunk_idx=0,
        pages=pages,
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


class _RecordingProvider:
    """Stub provider that records each call and returns scripted results."""

    name = "stub"
    model = "stub-1"

    def __init__(self, scripts: list[tuple[list[Finding], list[ChunkRecord]]]) -> None:
        self.scripts = list(scripts)
        self.calls: list[tuple[Page, ...]] = []
        self.attempts: list[int] = []

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        self.calls.append(pages)
        self.attempts.append(attempt)
        return self.scripts.pop(0)


def test_chunked_call_slices_pages_into_batches_even_divisor() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))  # 4 pages
    provider = _RecordingProvider(scripts=[([], [_chunk((1, 2))]), ([], [_chunk((3, 4))])])

    detect_redactions_chunked(
        provider, pages, ("person_name",), pages_per_batch=2, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert len(provider.calls) == 2
    assert tuple(p.number for p in provider.calls[0]) == (1, 2)
    assert tuple(p.number for p in provider.calls[1]) == (3, 4)


def test_chunked_call_slices_pages_into_batches_with_remainder() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))  # 4 pages
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2, 3))]),
            ([], [_chunk((4,))]),
        ]
    )

    detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=3, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert len(provider.calls) == 2
    assert tuple(p.number for p in provider.calls[0]) == (1, 2, 3)
    assert tuple(p.number for p in provider.calls[1]) == (4,)


def test_chunked_call_batch_size_larger_than_doc_makes_one_call() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))  # 4 pages
    provider = _RecordingProvider(scripts=[([], [_chunk((1, 2, 3, 4))])])

    detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=10, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert len(provider.calls) == 1
    assert tuple(p.number for p in provider.calls[0]) == (1, 2, 3, 4)


def test_chunked_call_aggregates_findings_in_batch_order() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))
    provider = _RecordingProvider(
        scripts=[
            ([_finding(1)], [_chunk((1, 2))]),
            ([_finding(3), _finding(4)], [_chunk((3, 4))]),
        ]
    )

    findings, _ = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert [f.page for f in findings] == [1, 3, 4]


def test_chunked_call_rejects_zero_or_negative_batch_size() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    provider = _RecordingProvider(scripts=[])
    with pytest.raises(ValueError, match="pages_per_batch"):
        detect_redactions_chunked(
            provider, (_page(1),), ("x",), pages_per_batch=0, retry_policy=DEFAULT_RETRY_POLICY
        )
    with pytest.raises(ValueError, match="pages_per_batch"):
        detect_redactions_chunked(
            provider, (_page(1),), ("x",), pages_per_batch=-1, retry_policy=DEFAULT_RETRY_POLICY
        )


def test_chunked_call_forwards_categories_instructions_and_seed() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    class _CapturingProvider:
        name = "stub"
        model = "stub"

        def __init__(self) -> None:
            self.last_kwargs: dict[str, Any] | None = None

        def detect_redactions(
            self,
            pages: tuple[Page, ...],
            llm_category_ids: tuple[str, ...],
            *,
            instructions: tuple[str, ...] = (),
            seed: int | None = None,
            attempt: int = 0,
        ) -> tuple[list[Finding], list[ChunkRecord]]:
            self.last_kwargs = {
                "llm_category_ids": llm_category_ids,
                "instructions": instructions,
                "seed": seed,
                "attempt": attempt,
            }
            return [], [_chunk(tuple(p.number for p in pages))]

    provider = _CapturingProvider()
    detect_redactions_chunked(
        provider,
        (_page(1),),
        ("email", "person_name"),
        instructions=("redact all names",),
        seed=42,
        pages_per_batch=1,
        retry_policy=DEFAULT_RETRY_POLICY,
    )

    assert provider.last_kwargs == {
        "llm_category_ids": ("email", "person_name"),
        "instructions": ("redact all names",),
        "seed": 42,
        "attempt": 0,
    }


def test_chunked_call_renumbers_chunk_idx_to_batch_position() -> None:
    """Providers hardcode chunk_idx=0; the orchestrator owns global ordering."""
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 7))  # 6 pages, batch=2 -> 3 batches
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2))]),
            ([], [_chunk((3, 4))]),
            ([], [_chunk((5, 6))]),
        ]
    )

    _, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert [c.chunk_idx for c in chunks] == [0, 1, 2]
    assert [c.pages for c in chunks] == [(1, 2), (3, 4), (5, 6)]


def test_chunked_call_retries_on_hard_failure_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chunks == [] is the hard-failure signal; orchestrator keeps retrying
    up to retry_policy.max_retries times with the policy's schedule."""
    from kuroi.core.chunking import detect_redactions_chunked

    sleeps: list[float] = []
    monkeypatch.setattr("kuroi.core.chunking.time.sleep", sleeps.append)

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(
        scripts=[
            ([], []),  # attempt 0: hard fail
            ([], []),  # attempt 1: hard fail
            ([_finding(1)], [_chunk((1, 2))]),  # attempt 2: succeeds
        ]
    )
    policy = RetryPolicy(max_retries=2, backoff=2.0, backoff_multiplier=2.0)

    findings, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2, retry_policy=policy
    )

    assert len(provider.calls) == 3
    assert [f.page for f in findings] == [1]
    assert [c.chunk_idx for c in chunks] == [0]
    # one sleep per retry actually taken; no sleep on the successful attempt
    assert sleeps == list(policy.schedule()[: len(sleeps)])
    assert provider.attempts == [0, 1, 2]


def test_chunked_call_aborts_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`policy.max_retries + 1` consecutive hard failures raise BatchError."""
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    policy = RetryPolicy(max_retries=2, backoff=2.0, backoff_multiplier=2.0)
    total_attempts = policy.max_retries + 1

    pages = tuple(_page(i) for i in range(1, 5))  # batch_idx=1 will fail
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2))]),  # batch 0 ok
            *[([], []) for _ in range(total_attempts)],  # batch 1: every attempt hard-fails
        ]
    )

    with pytest.raises(BatchError) as excinfo:
        detect_redactions_chunked(
            provider, pages, ("x",), pages_per_batch=2, retry_policy=policy
        )

    assert excinfo.value.batch_idx == 1
    assert excinfo.value.page_numbers == (3, 4)
    assert excinfo.value.attempts == total_attempts
    assert f"failed {total_attempts} times" in str(excinfo.value)
    assert "pages 3" in str(excinfo.value) and "4" in str(excinfo.value)


def test_chunked_call_uses_policy_schedule_between_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each retry sleeps for the matching entry in retry_policy.schedule()."""
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    sleeps: list[float] = []
    monkeypatch.setattr("kuroi.core.chunking.time.sleep", sleeps.append)

    policy = RetryPolicy(max_retries=3, backoff=1.0, backoff_multiplier=3.0)
    total_attempts = policy.max_retries + 1

    pages = (_page(1),)
    provider = _RecordingProvider(scripts=[([], []) for _ in range(total_attempts)])

    with pytest.raises(BatchError):
        detect_redactions_chunked(
            provider, pages, ("x",), pages_per_batch=1, retry_policy=policy
        )

    # one sleep per retry; no sleep after the final attempt before raising
    assert sleeps == [1.0, 3.0, 9.0]
    assert provider.attempts == list(range(total_attempts))


def test_chunked_call_does_not_retry_on_soft_empty_findings() -> None:
    """A chunk record with empty findings is a legitimate 'no redactions' answer."""
    from kuroi.core.chunking import detect_redactions_chunked

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(
        scripts=[([], [_chunk((1, 2))])]  # one call, soft-empty
    )

    findings, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2, retry_policy=DEFAULT_RETRY_POLICY
    )

    assert len(provider.calls) == 1
    assert findings == []
    assert len(chunks) == 1


def test_on_batch_start_fires_once_per_batch_before_provider_call() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2))]),
            ([], [_chunk((3, 4))]),
        ]
    )
    starts: list[tuple[int, int, tuple[int, ...]]] = []

    detect_redactions_chunked(
        provider,
        pages,
        ("x",),
        pages_per_batch=2,
        retry_policy=DEFAULT_RETRY_POLICY,
        on_batch_start=lambda i, n, ps: starts.append((i, n, ps)),
    )

    assert starts == [(0, 2, (1, 2)), (1, 2, (3, 4))]


def test_on_batch_complete_receives_renumbered_chunk() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2))]),
            ([], [_chunk((3, 4))]),
        ]
    )
    completes: list[tuple[int, int, tuple[int, ...], int]] = []

    def _capture(
        batch_idx: int,
        total: int,
        page_numbers: tuple[int, ...],
        chunk: ChunkRecord,
    ) -> None:
        completes.append((batch_idx, total, page_numbers, chunk.chunk_idx))

    detect_redactions_chunked(
        provider,
        pages,
        ("x",),
        pages_per_batch=2,
        retry_policy=DEFAULT_RETRY_POLICY,
        on_batch_complete=_capture,
    )

    assert completes == [(0, 2, (1, 2), 0), (1, 2, (3, 4), 1)]


def test_on_batch_complete_does_not_fire_on_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    policy = RetryPolicy(max_retries=2, backoff=2.0, backoff_multiplier=2.0)
    total_attempts = policy.max_retries + 1

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(scripts=[([], []) for _ in range(total_attempts)])
    completes: list[Any] = []

    with pytest.raises(BatchError):
        detect_redactions_chunked(
            provider,
            pages,
            ("x",),
            pages_per_batch=2,
            retry_policy=policy,
            on_batch_complete=lambda *args: completes.append(args),
        )

    assert completes == []


def test_chunked_call_with_zero_retries_aborts_on_first_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A RetryPolicy with max_retries=0 must call the provider exactly once and abort."""
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    sleeps: list[float] = []
    monkeypatch.setattr("kuroi.core.chunking.time.sleep", sleeps.append)

    pages = (_page(1),)
    provider = _RecordingProvider(scripts=[([], [])])  # one hard failure
    policy = RetryPolicy(max_retries=0, backoff=2.0, backoff_multiplier=2.0)

    with pytest.raises(BatchError) as excinfo:
        detect_redactions_chunked(
            provider, pages, ("x",), pages_per_batch=1, retry_policy=policy
        )

    assert len(provider.calls) == 1
    assert excinfo.value.attempts == 1
    assert sleeps == []  # never slept


def test_chunked_call_attempts_in_batch_error_match_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    policy = RetryPolicy(max_retries=4, backoff=0.1, backoff_multiplier=2.0)
    total_attempts = policy.max_retries + 1

    pages = (_page(1),)
    provider = _RecordingProvider(scripts=[([], []) for _ in range(total_attempts)])

    with pytest.raises(BatchError) as excinfo:
        detect_redactions_chunked(
            provider, pages, ("x",), pages_per_batch=1, retry_policy=policy
        )

    assert excinfo.value.attempts == total_attempts
    assert provider.attempts == list(range(total_attempts))


# ---------------------------------------------------------------------------
# _WorkItem and _IndexCounter foundation tests
# ---------------------------------------------------------------------------


def test_workitem_from_pages_has_no_word_range() -> None:
    """Default constructor for full-page batches: word_range is None."""
    from kuroi.core.chunking import _WorkItem

    pages = (_page(1), _page(2), _page(3))
    item = _WorkItem.from_pages(pages)

    assert item.pages == pages
    assert item.word_range is None


def test_workitem_with_word_range_records_slice() -> None:
    """Sub-page work items carry the original-page coordinates."""
    from kuroi.core.chunking import _WorkItem

    page = _page(1)
    item = _WorkItem(pages=(page,), word_range=(10, 50))

    assert item.word_range == (10, 50)


def test_index_counter_starts_at_zero() -> None:
    from kuroi.core.chunking import _IndexCounter

    counter = _IndexCounter()
    assert counter.next() == 0
    assert counter.next() == 1
    assert counter.next() == 2


def test_index_counter_independent_instances() -> None:
    """Each run gets its own counter; they don't share state."""
    from kuroi.core.chunking import _IndexCounter

    a = _IndexCounter()
    b = _IndexCounter()
    a.next()
    a.next()
    assert b.next() == 0  # b is unaffected by a


# ---------------------------------------------------------------------------
# _translate_indices tests
# ---------------------------------------------------------------------------


def test_translate_indices_passthrough_for_full_page_item() -> None:
    """word_range=None: findings are returned unchanged."""
    from kuroi.core.chunking import _translate_indices, _WorkItem

    item = _WorkItem.from_pages((_page(5),))
    inputs = [
        Finding(page=5, start=3, end=4, kind="x", confidence="high", source="llm"),
    ]

    out = _translate_indices(inputs, item)

    assert out == inputs


def test_translate_indices_shifts_start_and_end_by_word_range_offset() -> None:
    """Sub-page slice with offset 200: the model returned (start=3, end=5);
    the original-page position is (start=203, end=205)."""
    from kuroi.core.chunking import _translate_indices, _WorkItem

    item = _WorkItem(pages=(_page(5),), word_range=(200, 400))
    inputs = [
        Finding(page=5, start=3, end=5, kind="x", confidence="high", source="llm"),
        Finding(page=5, start=10, end=10, kind="y", confidence="medium", source="llm"),
    ]

    out = _translate_indices(inputs, item)

    assert [(f.start, f.end) for f in out] == [(203, 205), (210, 210)]
    # Other fields unchanged
    assert out[0].kind == "x"
    assert out[0].confidence == "high"
    assert out[1].source == "llm"


def test_translate_indices_preserves_page_number() -> None:
    """The page field is already correct because slice_page preserves
    page.number; the helper must not touch it."""
    from kuroi.core.chunking import _translate_indices, _WorkItem

    item = _WorkItem(pages=(_page(7),), word_range=(100, 200))
    inputs = [
        Finding(page=7, start=0, end=1, kind="x", confidence="high", source="llm"),
    ]

    out = _translate_indices(inputs, item)

    assert out[0].page == 7


# ---------------------------------------------------------------------------
# _dedupe tests
# ---------------------------------------------------------------------------


def test_dedupe_collapses_identical_findings() -> None:
    """Same (page, start, end, kind) → keep one."""
    from kuroi.core.chunking import _dedupe

    a = Finding(page=1, start=10, end=12, kind="X", confidence="high", source="llm")
    b = Finding(page=1, start=10, end=12, kind="X", confidence="high", source="llm")

    out = _dedupe([a, b])

    assert len(out) == 1


def test_dedupe_keeps_highest_confidence_on_collision() -> None:
    """When dedupe key collides, the higher-confidence finding wins."""
    from kuroi.core.chunking import _dedupe

    high = Finding(page=1, start=0, end=0, kind="X", confidence="high", source="llm")
    low = Finding(page=1, start=0, end=0, kind="X", confidence="low", source="llm")

    out_a = _dedupe([low, high])
    out_b = _dedupe([high, low])

    assert len(out_a) == 1 and out_a[0].confidence == "high"
    assert len(out_b) == 1 and out_b[0].confidence == "high"


def test_dedupe_preserves_overlapping_non_identical_findings() -> None:
    """Overlapping ranges of the same kind are different findings; keep both
    so the redaction step naturally redacts the union."""
    from kuroi.core.chunking import _dedupe

    a = Finding(page=1, start=10, end=12, kind="X", confidence="high", source="llm")
    b = Finding(page=1, start=11, end=13, kind="X", confidence="high", source="llm")

    out = _dedupe([a, b])

    assert len(out) == 2


def test_dedupe_does_not_merge_across_kinds() -> None:
    """Same range, different kind → keep both (genuine disagreement)."""
    from kuroi.core.chunking import _dedupe

    person = Finding(page=1, start=0, end=1, kind="person_name", confidence="high", source="llm")
    email = Finding(page=1, start=0, end=1, kind="email", confidence="high", source="llm")

    out = _dedupe([person, email])

    assert len(out) == 2


def test_dedupe_preserves_order_of_first_seen() -> None:
    """Dedupe should be deterministic. Within a colliding key group, the
    earliest-arriving finding (after applying the highest-confidence rule)
    is kept; first-seen ordering across groups is preserved."""
    from kuroi.core.chunking import _dedupe

    f1 = Finding(page=1, start=0, end=0, kind="a", confidence="high", source="llm")
    f2 = Finding(page=1, start=1, end=1, kind="b", confidence="high", source="llm")
    f3 = Finding(page=1, start=0, end=0, kind="a", confidence="low", source="llm")

    out = _dedupe([f1, f2, f3])

    assert [(f.start, f.kind) for f in out] == [(0, "a"), (1, "b")]


# ---------------------------------------------------------------------------
# _halve tests
# ---------------------------------------------------------------------------


def test_halve_splits_multi_page_item_evenly() -> None:
    """4 pages → [[p1,p2], [p3,p4]]. Both children have word_range=None."""
    from kuroi.core.chunking import _halve, _WorkItem

    pages = tuple(_page(i) for i in range(1, 5))
    item = _WorkItem.from_pages(pages)

    left, right = _halve(item)

    assert tuple(p.number for p in left.pages) == (1, 2)
    assert tuple(p.number for p in right.pages) == (3, 4)
    assert left.word_range is None
    assert right.word_range is None


def test_halve_splits_multi_page_item_with_odd_count() -> None:
    """3 pages → [[p1], [p2,p3]]. Left half is the smaller half."""
    from kuroi.core.chunking import _halve, _WorkItem

    pages = (_page(1), _page(2), _page(3))
    item = _WorkItem.from_pages(pages)

    left, right = _halve(item)

    assert tuple(p.number for p in left.pages) == (1,)
    assert tuple(p.number for p in right.pages) == (2, 3)


def test_halve_two_page_item_yields_two_single_page_children() -> None:
    """2 pages is the smallest multi-page case: → [[p1], [p2]]."""
    from kuroi.core.chunking import _halve, _WorkItem

    item = _WorkItem.from_pages((_page(1), _page(2)))

    left, right = _halve(item)

    assert tuple(p.number for p in left.pages) == (1,)
    assert tuple(p.number for p in right.pages) == (2,)


def _page_with_words(num: int, n_words: int) -> Page:
    return Page(
        number=num,
        words=tuple(
            Word(idx=i, text=f"w{i}", bbox=(float(i), 0.0, float(i + 1), 1.0))
            for i in range(n_words)
        ),
    )


def test_halve_single_page_produces_overlapping_halves() -> None:
    """1000-word page with M=500, OVERLAP=50:
    left  = words[0..550]   (sliced page has 550 words, idx 0..549)
    right = words[450..1000] (sliced page has 550 words, idx 0..549)
    Both word_ranges record the original-page coordinates.
    """
    from kuroi.core.chunking import _halve, OVERLAP_WORDS, _WorkItem

    page = _page_with_words(num=3, n_words=1000)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    expected_mid = 500
    assert left.word_range == (0, expected_mid + OVERLAP_WORDS)
    assert right.word_range == (expected_mid - OVERLAP_WORDS, 1000)
    # Both halves carry exactly one page (the sliced page).
    assert len(left.pages) == 1 and len(right.pages) == 1
    # Sliced pages preserve page.number
    assert left.pages[0].number == 3
    assert right.pages[0].number == 3
    # Sliced word counts match the ranges
    assert len(left.pages[0].words) == expected_mid + OVERLAP_WORDS
    assert len(right.pages[0].words) == 1000 - (expected_mid - OVERLAP_WORDS)


def test_halve_single_page_overlap_includes_original_text_at_boundary() -> None:
    """The overlap zone (M-O .. M+O) must appear in the sliced text of both
    halves, so a boundary-straddling entity survives in at least one."""
    from kuroi.core.chunking import _halve, OVERLAP_WORDS, _WorkItem

    page = _page_with_words(num=1, n_words=200)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    # Original M=100, OVERLAP=50, overlap zone is original words[50..150]
    boundary_texts = {f"w{i}" for i in range(100 - OVERLAP_WORDS, 100 + OVERLAP_WORDS)}
    left_texts = {w.text for w in left.pages[0].words}
    right_texts = {w.text for w in right.pages[0].words}

    assert boundary_texts.issubset(left_texts)
    assert boundary_texts.issubset(right_texts)


def test_halve_single_page_re_indexes_words_to_zero_base() -> None:
    """The sliced page's Word.idx values restart at 0 in each child."""
    from kuroi.core.chunking import _halve, _WorkItem

    page = _page_with_words(num=1, n_words=300)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    assert left.pages[0].words[0].idx == 0
    assert right.pages[0].words[0].idx == 0


# ---------------------------------------------------------------------------
# _halve floor + BatchError diagnostics
# ---------------------------------------------------------------------------


def test_halve_raises_at_floor_for_too_small_single_page() -> None:
    """N <= 2 * OVERLAP_WORDS → no useful subdivision possible."""
    import pytest

    from kuroi.core.chunking import _halve, BatchError, _WorkItem

    page = _page_with_words(num=1, n_words=80)  # 80 <= 2*50
    item = _WorkItem.from_pages((page,))

    with pytest.raises(BatchError) as exc_info:
        _halve(item)

    err = exc_info.value
    assert err.last_failed_word_range == (0, 80)


def test_halve_raises_at_floor_when_halves_would_be_too_small() -> None:
    """N=99, OVERLAP=50: mid=49, left would be 99 words, right would be
    99 words — neither is strictly smaller than the parent, so floor."""
    import pytest

    from kuroi.core.chunking import _halve, BatchError, _WorkItem

    page = _page_with_words(num=1, n_words=99)
    item = _WorkItem.from_pages((page,))

    with pytest.raises(BatchError):
        _halve(item)


def test_halve_does_not_raise_just_above_the_floor() -> None:
    """N=101, OVERLAP=50: mid=50, halves are 100 words each — strictly
    smaller than 101. Floor not reached."""
    from kuroi.core.chunking import _halve, _WorkItem

    page = _page_with_words(num=1, n_words=101)
    item = _WorkItem.from_pages((page,))

    left, right = _halve(item)

    assert len(left.pages[0].words) == 100
    assert len(right.pages[0].words) == 100


def test_batch_error_carries_subdivision_diagnostics_when_set() -> None:
    """The new optional fields default cleanly when not set, and round-trip
    when explicitly populated."""
    from kuroi.core.chunking import BatchError

    err = BatchError(
        batch_idx=4,
        page_numbers=(41,),
        attempts=3,
        subdivision_levels=7,
        last_failed_word_range=(0, 100),
        last_prompt_chars=4823,
    )

    assert err.subdivision_levels == 7
    assert err.last_failed_word_range == (0, 100)
    assert err.last_prompt_chars == 4823
    msg = str(err)
    assert "page 41" in msg
    assert "100 words" in msg
    assert "Tried 7 levels" in msg


def test_batch_error_legacy_message_for_multi_page_failure() -> None:
    """When subdivision diagnostics aren't set (e.g. simple multi-page
    BatchError surfaced from the retry path), keep the old single-line
    message for backward compatibility with existing CLI handling."""
    from kuroi.core.chunking import BatchError

    err = BatchError(batch_idx=3, page_numbers=(13, 14, 15), attempts=3)

    msg = str(err)
    assert msg == "Batch 4 (pages 13–15) failed 3 times and was aborted."  # noqa: RUF001
