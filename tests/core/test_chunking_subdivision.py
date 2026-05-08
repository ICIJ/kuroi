"""End-to-end tests for the recursive subdivision path in
core.chunking.detect_redactions_chunked. Uses a ScriptedFailureProvider
that fails on prompts above a threshold size and succeeds otherwise,
to drive the orchestrator through real subdivision trees."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.chunking import (
    OVERLAP_WORDS,
    BatchError,
    detect_redactions_chunked,
)
from kuroi.core.config import RetryPolicy
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, Word
from kuroi.providers._shared import build_user_prompt


def _page(num: int, n_words: int = 1) -> Page:
    return Page(
        number=num,
        words=tuple(
            Word(idx=i, text=f"p{num}_w{i}", bbox=(float(i), 0.0, float(i + 1), 1.0))
            for i in range(n_words)
        ),
    )


def _chunk(pages: tuple[int, ...], prompt_chars: int = 100) -> ChunkRecord:
    """Build a ChunkRecord matching what real providers set.

    Real providers populate `pages` from `tuple(p.number for p in batch)`.
    The chunker's renumber step only overrides `chunk_idx` and
    `page_word_range`, so this fixture must set `pages` to the real
    page numbers for tests that assert on them.
    """
    return ChunkRecord(
        chunk_idx=0,
        pages=pages,
        temperature=0.0,
        seed_requested=None,
        seed_honored=False,
        system_fingerprint=None,
        prompt_sha256="a" * 64,
        response_sha256="b" * 64,
        tokens_in=prompt_chars,
        tokens_out=prompt_chars // 4,
        duration_ms=10,
    )


class ScriptedFailureProvider:
    """Provider stub that fails iff the prompt exceeds `fail_above_chars`.

    On failure: returns ([], []) — the chunker's "subdivide" signal.
    On success: returns the configured findings (translated to absolute
    word indices via the test's setup) and a single ChunkRecord.

    Records every attempted call's prompt so tests can assert on the
    actual data the model would have seen.
    """

    name = "scripted"
    model = "scripted-1"

    def __init__(
        self,
        fail_above_chars: int,
        findings_for: Callable[[tuple[Page, ...]], list[Finding]] | None = None,
    ) -> None:
        self.fail_above_chars = fail_above_chars
        self.findings_for = findings_for or (lambda _pages: [])
        self.calls: list[tuple[tuple[Page, ...], int, str]] = []

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
        prompt = build_user_prompt(pages, llm_category_ids, instructions)
        self.calls.append((pages, attempt, prompt))
        if len(prompt) > self.fail_above_chars:
            return [], []
        page_numbers = tuple(p.number for p in pages)
        return (
            self.findings_for(pages),
            [_chunk(pages=page_numbers, prompt_chars=len(prompt))],
        )


# A retry policy with no sleeps, used by every test below to keep them fast.
NO_SLEEP_POLICY = RetryPolicy(max_retries=0, backoff=0.0, backoff_multiplier=1.0)


def test_multi_page_batch_halves_on_failure() -> None:
    """4-page batch; provider fails on any 2+ page prompt. The orchestrator
    must subdivide [1,2,3,4] → [1,2]+[3,4] → [1]+[2]+[3]+[4], producing 4
    successful single-page calls and a monotonic chunk_idx sequence."""
    pages = tuple(_page(i, n_words=5) for i in range(1, 5))
    # Calibrate the threshold so single-page prompts succeed but 2+ pages
    # always exceed it.
    one_page_prompt_len = len(build_user_prompt((pages[0],), ("x",)))
    threshold = one_page_prompt_len + 10  # any 2+ page prompt is well above

    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    findings, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=4, retry_policy=NO_SLEEP_POLICY
    )

    # Exactly 4 successful single-page records, monotonic chunk_idx
    assert len(chunks) == 4
    assert [c.chunk_idx for c in chunks] == [0, 1, 2, 3]
    assert [c.pages for c in chunks] == [(1,), (2,), (3,), (4,)]
    assert findings == []


def test_single_page_halves_with_overlap_text_visible_in_both_halves() -> None:
    """1-page batch with 200 words; provider fails on any prompt longer than
    a half-with-overlap. After subdivision, the boundary words (text content
    from M-OVERLAP..M+OVERLAP in the original page) must appear in BOTH
    halves' captured prompts."""
    page = _page(7, n_words=200)
    pages = (page,)
    # Halving a 200-word page yields two 150-word halves (n//2 + OVERLAP).
    # Threshold lets the 150-word halves through but rejects the 200-word
    # full-page prompt. Use a value strictly between those two prompt sizes.
    full_len = len(build_user_prompt((page,), ("x",)))
    half_with_overlap_prompt_len = full_len - 1
    provider = ScriptedFailureProvider(fail_above_chars=half_with_overlap_prompt_len)

    detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    # Two successful sub-calls (the halves); plus one initial failed call.
    success_prompts = [
        prompt
        for (_pgs, _att, prompt) in provider.calls
        if len(prompt) <= half_with_overlap_prompt_len
    ]
    assert len(success_prompts) == 2

    # Boundary text words: original page words at indices [50..150).
    boundary_texts = {f"p7_w{i}" for i in range(100 - OVERLAP_WORDS, 100 + OVERLAP_WORDS)}
    for prompt in success_prompts:
        for text in boundary_texts:
            assert text in prompt, (
                f"boundary word {text!r} missing from one half's prompt"
            )


def test_overlap_dedupes_boundary_findings() -> None:
    """Both halves return a finding for the same overlap-zone word range;
    the final result has a single finding with the higher confidence."""
    page = _page(1, n_words=200)

    # Both halves report a finding at original-page words 95..100 — squarely
    # in the overlap zone. The provider here returns findings in the
    # *synthetic* (sliced) page's coordinates; the chunker translates back.
    def findings_for(pages: tuple[Page, ...]) -> list[Finding]:
        # Each half is a synthetic page with re-indexed words 0..N-1.
        # We invert by checking the first word's text to know which half
        # we're in (left starts at "p1_w0", right starts at "p1_w50").
        first_text = pages[0].words[0].text
        if first_text == "p1_w0":
            # Left half: original word 95 maps to local index 95
            return [Finding(page=1, start=95, end=100, kind="X", confidence="high", source="llm")]
        else:
            # Right half: original word 95 maps to local index 95 - 50 = 45
            return [Finding(page=1, start=45, end=50, kind="X", confidence="medium", source="llm")]

    # Threshold lets the 150-word halves through but rejects the 200-word
    # full-page prompt (so we get exactly one round of subdivision).
    full_len = len(build_user_prompt((page,), ("x",)))
    threshold = full_len - 1
    provider = ScriptedFailureProvider(fail_above_chars=threshold, findings_for=findings_for)

    findings, _ = detect_redactions_chunked(
        provider, (page,), ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    assert len(findings) == 1
    assert findings[0].page == 1
    assert findings[0].start == 95
    assert findings[0].end == 100
    assert findings[0].confidence == "high"  # higher confidence wins


def test_overlapping_non_identical_findings_preserved() -> None:
    """End-to-end: two halves report findings at overlapping-but-distinct
    word ranges in the boundary zone. Dedupe keeps both — same-kind
    findings at different ranges are genuine disagreement worth surfacing
    (the redaction step naturally redacts the union)."""
    page = _page(1, n_words=200)

    # Both halves cover the boundary zone (50..150 in original-page coords).
    # We pick ranges that overlap but are not identical:
    #   Half A: original (95, 97) — local 95..97 in left half (word_range 0..150)
    #   Half B: original (96, 98) — local 46..48 in right half (word_range 50..200)
    def findings_for(pages: tuple[Page, ...]) -> list[Finding]:
        first_text = pages[0].words[0].text
        if first_text == "p1_w0":  # left half
            return [Finding(page=1, start=95, end=97, kind="X", confidence="high", source="llm")]
        return [Finding(page=1, start=46, end=48, kind="X", confidence="high", source="llm")]

    # Threshold lets the 150-word halves through but rejects the 200-word
    # full-page prompt (so we get exactly one round of subdivision).
    full_len = len(build_user_prompt((page,), ("x",)))
    threshold = full_len - 1
    provider = ScriptedFailureProvider(fail_above_chars=threshold, findings_for=findings_for)

    findings, _ = detect_redactions_chunked(
        provider, (page,), ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    # Both kept — overlapping ranges of the same kind are different findings
    assert len(findings) == 2
    ranges = sorted((f.start, f.end) for f in findings)
    assert ranges == [(95, 97), (96, 98)]


def test_floor_raises_batch_error_with_diagnostics() -> None:
    """Single page with 80 words; provider always fails. BatchError must
    carry the page number, the failed word range, and the depth."""
    page = _page(41, n_words=80)
    provider = ScriptedFailureProvider(fail_above_chars=0)  # always fails

    with pytest.raises(BatchError) as exc_info:
        detect_redactions_chunked(
            provider, (page,), ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
        )

    err = exc_info.value
    assert err.page_numbers == (41,)
    assert err.last_failed_word_range == (0, 80)
    assert err.subdivision_levels >= 1
    msg = str(err)
    assert "page 41" in msg
    assert "80 words" in msg


def test_chunk_idx_globally_monotonic_across_subdivision() -> None:
    """Multi-page batch where pages 1-2 succeed but page 3 subdivides. The
    final chunk_idx sequence is 0..N-1 with no duplicates and no gaps."""
    pages = (
        _page(1, n_words=5),
        _page(2, n_words=5),
        _page(3, n_words=200),  # this one will subdivide
    )
    page3_full_prompt = len(build_user_prompt((pages[2],), ("x",)))
    # Threshold lets the small pages and the 150-word halves of page 3
    # through, but rejects the full 200-word page-3 prompt.
    threshold = page3_full_prompt - 1
    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    _, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    chunk_ids = [c.chunk_idx for c in chunks]
    assert chunk_ids == list(range(len(chunks)))
    assert len(chunks) == 4  # pages 1, 2, plus 2 halves of page 3


def test_page_word_range_recorded_for_sub_page_calls() -> None:
    """Sub-page chunks have page_word_range != None in original-page
    coordinates; full-page chunks have page_word_range = None."""
    pages = (_page(1, n_words=5), _page(2, n_words=200))
    full_prompt_p2 = len(build_user_prompt((pages[1],), ("x",)))
    # Threshold lets page 1 (small) and 150-word halves of page 2 succeed,
    # but rejects the 200-word full page-2 prompt.
    threshold = full_prompt_p2 - 1
    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    _, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=1, retry_policy=NO_SLEEP_POLICY
    )

    # First chunk is page 1 full-page; subsequent chunks are page 2 halves.
    page1_chunks = [c for c in chunks if c.pages == (1,)]
    page2_chunks = [c for c in chunks if c.pages == (2,)]
    assert all(c.page_word_range is None for c in page1_chunks)
    assert all(c.page_word_range is not None for c in page2_chunks)
    # The two page-2 halves cover the full word range with overlap
    page2_ranges = [c.page_word_range for c in page2_chunks]
    assert all(r is not None for r in page2_ranges)
    starts = sorted(r[0] for r in page2_ranges if r is not None)
    ends = sorted(r[1] for r in page2_ranges if r is not None)
    assert starts[0] == 0
    assert ends[-1] == 200


def test_subdivision_runs_after_retries_not_before() -> None:
    """Retry budget is consumed first. Provider scripted to fail twice then
    succeed; with RetryPolicy(max_retries=2), the batch succeeds on attempt
    2 without subdivision. Therefore total successful chunks == 1."""
    page = _page(1, n_words=10)

    class TransientProvider:
        name = "transient"
        model = "stub-1"

        def __init__(self) -> None:
            self.attempt_count = 0
            self.calls: list[int] = []

        def detect_redactions(
            self,
            pages: tuple[Page, ...],
            llm_category_ids: tuple[str, ...],
            *,
            instructions: tuple[str, ...] = (),
            seed: int | None = None,
            attempt: int = 0,
            layout_aware: bool = False,
        ) -> tuple[list[Finding], list[ChunkRecord]]:
            self.calls.append(attempt)
            if self.attempt_count < 2:
                self.attempt_count += 1
                return [], []
            return [], [_chunk(pages=tuple(p.number for p in pages))]

    provider = TransientProvider()
    policy = RetryPolicy(max_retries=2, backoff=0.0, backoff_multiplier=1.0)

    _, chunks = detect_redactions_chunked(
        provider, (page,), ("x",), pages_per_batch=1, retry_policy=policy
    )

    assert len(chunks) == 1
    assert provider.calls == [0, 1, 2]  # full retry budget consumed first


def test_progress_callbacks_fire_at_batch_boundary_only() -> None:
    """Subdivision is internal: on_batch_start / on_batch_complete fire
    once per top-level batch even when subdivision occurs underneath."""
    pages = (_page(1, n_words=5), _page(2, n_words=200))
    full_prompt_p2 = len(build_user_prompt((pages[1],), ("x",)))
    # Threshold lets page 1 (small) and 150-word halves of page 2 succeed,
    # but rejects the 200-word full page-2 prompt.
    threshold = full_prompt_p2 - 1
    provider = ScriptedFailureProvider(fail_above_chars=threshold)

    starts: list[int] = []
    completes: list[int] = []

    def on_start(idx: int, total: int, _pn: tuple[int, ...]) -> None:
        starts.append(idx)

    def on_complete(idx: int, total: int, _pn: tuple[int, ...], _ck: ChunkRecord) -> None:
        completes.append(idx)

    detect_redactions_chunked(
        provider,
        pages,
        ("x",),
        pages_per_batch=1,
        retry_policy=NO_SLEEP_POLICY,
        on_batch_start=on_start,
        on_batch_complete=on_complete,
    )

    # Two top-level batches → two starts, two completes — even though
    # batch 2 produced multiple sub-records under the hood.
    assert starts == [0, 1]
    assert completes == [0, 1]
