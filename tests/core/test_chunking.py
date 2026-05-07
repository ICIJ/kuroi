"""Tests for the per-batch chunking orchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from kuroi.core.audit_records import ChunkRecord
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

    detect_redactions_chunked(provider, pages, ("person_name",), pages_per_batch=2)

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

    detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=3)

    assert len(provider.calls) == 2
    assert tuple(p.number for p in provider.calls[0]) == (1, 2, 3)
    assert tuple(p.number for p in provider.calls[1]) == (4,)


def test_chunked_call_batch_size_larger_than_doc_makes_one_call() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))  # 4 pages
    provider = _RecordingProvider(scripts=[([], [_chunk((1, 2, 3, 4))])])

    detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=10)

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

    findings, _ = detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=2)

    assert [f.page for f in findings] == [1, 3, 4]


def test_chunked_call_rejects_zero_or_negative_batch_size() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    provider = _RecordingProvider(scripts=[])
    with pytest.raises(ValueError, match="pages_per_batch"):
        detect_redactions_chunked(provider, (_page(1),), ("x",), pages_per_batch=0)
    with pytest.raises(ValueError, match="pages_per_batch"):
        detect_redactions_chunked(provider, (_page(1),), ("x",), pages_per_batch=-1)


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

    _, chunks = detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=2)

    assert [c.chunk_idx for c in chunks] == [0, 1, 2]
    assert [c.pages for c in chunks] == [(1, 2), (3, 4), (5, 6)]


def test_chunked_call_retries_on_hard_failure_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chunks == [] is the hard-failure signal; orchestrator keeps retrying
    up to MAX_ATTEMPTS - 1 retries with exponential backoff."""
    import kuroi.core.chunking as chunking_mod
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

    findings, chunks = detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=2)

    assert len(provider.calls) == 3
    assert [f.page for f in findings] == [1]
    assert [c.chunk_idx for c in chunks] == [0]
    # backoffs are sliced from the front of RETRY_BACKOFFS_SECONDS
    assert sleeps == list(chunking_mod.RETRY_BACKOFFS_SECONDS[: len(sleeps)])
    assert provider.attempts == [0, 1, 2]


def test_chunked_call_aborts_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAX_ATTEMPTS consecutive hard failures raise BatchError."""
    from kuroi.core.chunking import (
        MAX_ATTEMPTS,
        BatchError,
        detect_redactions_chunked,
    )

    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    pages = tuple(_page(i) for i in range(1, 5))  # batch_idx=1 will fail
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2))]),  # batch 0 ok
            *[([], []) for _ in range(MAX_ATTEMPTS)],  # batch 1: every attempt hard-fails
        ]
    )

    with pytest.raises(BatchError) as excinfo:
        detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=2)

    assert excinfo.value.batch_idx == 1
    assert excinfo.value.page_numbers == (3, 4)
    assert excinfo.value.attempts == MAX_ATTEMPTS
    assert f"failed {MAX_ATTEMPTS} times" in str(excinfo.value)
    assert "pages 3" in str(excinfo.value) and "4" in str(excinfo.value)


def test_chunked_call_uses_exponential_backoff_between_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each retry sleeps for the matching entry in RETRY_BACKOFFS_SECONDS."""
    import kuroi.core.chunking as chunking_mod
    from kuroi.core.chunking import (
        MAX_ATTEMPTS,
        BatchError,
        detect_redactions_chunked,
    )

    sleeps: list[float] = []
    monkeypatch.setattr("kuroi.core.chunking.time.sleep", sleeps.append)

    pages = (_page(1),)
    provider = _RecordingProvider(scripts=[([], []) for _ in range(MAX_ATTEMPTS)])

    with pytest.raises(BatchError):
        detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=1)

    # one sleep per retry; no sleep after the final attempt before raising
    assert sleeps == list(chunking_mod.RETRY_BACKOFFS_SECONDS)
    assert provider.attempts == list(range(MAX_ATTEMPTS))


def test_chunked_call_does_not_retry_on_soft_empty_findings() -> None:
    """A chunk record with empty findings is a legitimate 'no redactions' answer."""
    from kuroi.core.chunking import detect_redactions_chunked

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(
        scripts=[([], [_chunk((1, 2))])]  # one call, soft-empty
    )

    findings, chunks = detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=2)

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
        on_batch_complete=_capture,
    )

    assert completes == [(0, 2, (1, 2), 0), (1, 2, (3, 4), 1)]


def test_on_batch_complete_does_not_fire_on_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kuroi.core.chunking import MAX_ATTEMPTS, BatchError, detect_redactions_chunked

    monkeypatch.setattr("kuroi.core.chunking.time.sleep", lambda _: None)

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(scripts=[([], []) for _ in range(MAX_ATTEMPTS)])
    completes: list[Any] = []

    with pytest.raises(BatchError):
        detect_redactions_chunked(
            provider,
            pages,
            ("x",),
            pages_per_batch=2,
            on_batch_complete=lambda *args: completes.append(args),
        )

    assert completes == []
