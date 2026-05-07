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
    return Finding(
        page=page, start=0, end=0, kind="x", confidence="high", source="llm"
    )


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

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        self.calls.append(pages)
        return self.scripts.pop(0)


def test_chunked_call_slices_pages_into_batches_even_divisor() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    pages = tuple(_page(i) for i in range(1, 5))  # 4 pages
    provider = _RecordingProvider(
        scripts=[([], [_chunk((1, 2))]), ([], [_chunk((3, 4))])]
    )

    detect_redactions_chunked(
        provider, pages, ("person_name",), pages_per_batch=2
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

    findings, _ = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2
    )

    assert [f.page for f in findings] == [1, 3, 4]


def test_chunked_call_rejects_zero_or_negative_batch_size() -> None:
    from kuroi.core.chunking import detect_redactions_chunked

    provider = _RecordingProvider(scripts=[])
    with pytest.raises(ValueError, match="pages_per_batch"):
        detect_redactions_chunked(
            provider, (_page(1),), ("x",), pages_per_batch=0
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
        ) -> tuple[list[Finding], list[ChunkRecord]]:
            self.last_kwargs = {
                "llm_category_ids": llm_category_ids,
                "instructions": instructions,
                "seed": seed,
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
    }
