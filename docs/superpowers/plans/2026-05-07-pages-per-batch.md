# Per-Batch Page Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `--pages-per-batch N` flag to `kuroi run` that splits LLM analysis into batches of N pages, with retry-once-then-abort on hard failures, so users can avoid silent context-window truncation on small models.

**Architecture:** A new `core/chunking.py` orchestrator slices pages into batches, calls the unchanged `Provider.detect_redactions` once per batch, retries once on hard failures (provider returned `chunks == []`), and renumbers `ChunkRecord.chunk_idx` to its global batch position. `cli/run.py` adds the new flag and wires two progress callbacks for the per-batch status line. The default (`--pages-per-batch 0`) routes through the existing single-call code path with zero behavior change.

**Tech Stack:** Python 3.12, Typer, Anthropic SDK, httpx (Ollama), pytest, dataclasses, logging.

**Spec:** `docs/superpowers/specs/2026-05-07-pages-per-batch-design.md`

---

## File map

| File | Change |
|------|--------|
| `src/kuroi/core/chunking.py` | New — `BatchError`, `detect_redactions_chunked` orchestrator |
| `src/kuroi/cli/run.py` | Modify — add `--pages-per-batch` flag, wire orchestrator + progress UI |
| `tests/core/test_chunking.py` | New — unit tests for orchestrator (slicing, renumbering, retry, abort, callbacks) |
| `tests/cli/test_run.py` | Modify — CLI integration test for `--pages-per-batch` |

---

### Task 1: Create chunking orchestrator with slicing and findings aggregation

**Files:**
- Create: `src/kuroi/core/chunking.py`
- Create: `tests/core/test_chunking.py`

- [ ] **Step 1: Write failing tests**

Create `tests/core/test_chunking.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: All six tests fail with `ModuleNotFoundError: No module named 'kuroi.core.chunking'`.

- [ ] **Step 3: Create the orchestrator module**

Create `src/kuroi/core/chunking.py`:

```python
"""Per-batch chunking orchestrator for LLM redaction calls.

Slices a document's pages into batches of N, dispatches one
`Provider.detect_redactions` call per batch, and aggregates the resulting
findings and chunk records. Retry, abort, and progress concerns live here so
provider implementations stay single-call.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from kuroi.core.audit_records import ChunkRecord
from kuroi.core.findings import Finding
from kuroi.core.pdf import Page
from kuroi.providers.base import Provider

logger = logging.getLogger("kuroi.core.chunking")


def detect_redactions_chunked(
    provider: Provider,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    pages_per_batch: int,
    on_batch_start: Callable[[int, int, tuple[int, ...]], None] | None = None,
    on_batch_complete: Callable[[int, int, tuple[int, ...], ChunkRecord], None] | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
    if pages_per_batch < 1:
        raise ValueError(f"pages_per_batch must be >= 1, got {pages_per_batch}")

    total_batches = math.ceil(len(pages) / pages_per_batch)
    aggregate_findings: list[Finding] = []
    aggregate_chunks: list[ChunkRecord] = []

    for batch_idx in range(total_batches):
        offset = batch_idx * pages_per_batch
        batch = pages[offset : offset + pages_per_batch]
        page_numbers = tuple(p.number for p in batch)

        if on_batch_start is not None:
            on_batch_start(batch_idx, total_batches, page_numbers)

        findings, chunks = provider.detect_redactions(
            batch,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
        )

        aggregate_findings.extend(findings)
        aggregate_chunks.extend(chunks)

        if on_batch_complete is not None and chunks:
            on_batch_complete(batch_idx, total_batches, page_numbers, chunks[0])

    return aggregate_findings, aggregate_chunks
```

The `on_batch_start` / `on_batch_complete` parameters are accepted now but exercised by later tasks. Including them here avoids signature churn.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: All six tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): add per-batch orchestrator skeleton

New core/chunking.py wraps a Provider with batch-slicing logic.
This task covers slicing (with even divisor, remainder, and
oversized batch cases), kwarg pass-through (categories, instructions,
seed), and findings aggregation in batch order. Retry and progress
callbacks land in subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Renumber `chunk_idx` to global batch position

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Modify: `tests/core/test_chunking.py`

- [ ] **Step 1: Write failing test**

Append to `tests/core/test_chunking.py`:

```python
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
        provider, pages, ("x",), pages_per_batch=2
    )

    assert [c.chunk_idx for c in chunks] == [0, 1, 2]
    assert [c.pages for c in chunks] == [(1, 2), (3, 4), (5, 6)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_chunking.py::test_chunked_call_renumbers_chunk_idx_to_batch_position -v`
Expected: FAIL — all chunk_idx values are `0` because providers hardcode that.

- [ ] **Step 3: Add renumbering with `dataclasses.replace`**

In `src/kuroi/core/chunking.py`, add the import:

```python
from dataclasses import replace
```

Replace the inner-loop block:

```python
        findings, chunks = provider.detect_redactions(
            batch,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
        )

        aggregate_findings.extend(findings)
        aggregate_chunks.extend(chunks)

        if on_batch_complete is not None and chunks:
            on_batch_complete(batch_idx, total_batches, page_numbers, chunks[0])
```

with:

```python
        findings, chunks = provider.detect_redactions(
            batch,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
        )

        renumbered = [replace(c, chunk_idx=batch_idx) for c in chunks]
        aggregate_findings.extend(findings)
        aggregate_chunks.extend(renumbered)

        if on_batch_complete is not None and renumbered:
            on_batch_complete(batch_idx, total_batches, page_numbers, renumbered[0])
```

- [ ] **Step 4: Run all chunking tests**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: All seven tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): renumber chunk_idx to global batch position

Providers hardcode chunk_idx=0 because they were single-call. The
orchestrator now owns global ordering and uses dataclasses.replace
to assign each batch's chunk record its position in the run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Retry once on hard failure; abort with `BatchError` on double-fail

**Files:**
- Modify: `src/kuroi/core/chunking.py`
- Modify: `tests/core/test_chunking.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/core/test_chunking.py`:

```python
def test_chunked_call_retries_once_on_hard_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chunks == [] is the hard-failure signal; orchestrator retries once."""
    import kuroi.core.chunking as chunking_mod
    from kuroi.core.chunking import detect_redactions_chunked

    sleeps: list[float] = []
    monkeypatch.setattr(chunking_mod.time, "sleep", sleeps.append)

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(
        scripts=[
            ([], []),  # hard fail
            ([_finding(1)], [_chunk((1, 2))]),  # retry succeeds
        ]
    )

    findings, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2
    )

    assert len(provider.calls) == 2
    assert [f.page for f in findings] == [1]
    assert [c.chunk_idx for c in chunks] == [0]
    assert sleeps == [chunking_mod.RETRY_BACKOFF_SECONDS]


def test_chunked_call_aborts_after_two_hard_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kuroi.core.chunking as chunking_mod
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    monkeypatch.setattr(chunking_mod.time, "sleep", lambda _: None)

    pages = tuple(_page(i) for i in range(1, 5))  # batch_idx=1 will fail
    provider = _RecordingProvider(
        scripts=[
            ([], [_chunk((1, 2))]),  # batch 0 ok
            ([], []),                # batch 1 hard fail
            ([], []),                # batch 1 retry hard fail
        ]
    )

    with pytest.raises(BatchError) as excinfo:
        detect_redactions_chunked(provider, pages, ("x",), pages_per_batch=2)

    assert excinfo.value.batch_idx == 1
    assert excinfo.value.page_numbers == (3, 4)
    assert "pages 3" in str(excinfo.value) and "4" in str(excinfo.value)


def test_chunked_call_does_not_retry_on_soft_empty_findings() -> None:
    """A chunk record with empty findings is a legitimate 'no redactions' answer."""
    from kuroi.core.chunking import detect_redactions_chunked

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(
        scripts=[([], [_chunk((1, 2))])]  # one call, soft-empty
    )

    findings, chunks = detect_redactions_chunked(
        provider, pages, ("x",), pages_per_batch=2
    )

    assert len(provider.calls) == 1
    assert findings == []
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: The two new retry/abort tests fail (one of them with `ImportError: cannot import name 'BatchError'`); the soft-empty test should already pass (orchestrator does nothing wrong with a single soft-empty result), but verify before proceeding.

- [ ] **Step 3: Add `BatchError`, `RETRY_BACKOFF_SECONDS`, retry loop**

In `src/kuroi/core/chunking.py`, add `import time` at the top alongside other imports.

After the `logger = ...` line, add:

```python
RETRY_BACKOFF_SECONDS = 2.0


class BatchError(Exception):
    """Raised when a batch fails twice (initial call + retry)."""

    def __init__(self, batch_idx: int, page_numbers: tuple[int, ...]) -> None:
        self.batch_idx = batch_idx
        self.page_numbers = page_numbers
        if len(page_numbers) > 1:
            page_range = f"{page_numbers[0]}-{page_numbers[-1]}"
        else:
            page_range = f"{page_numbers[0]}"
        super().__init__(
            f"Batch {batch_idx + 1} (pages {page_range}) failed twice and was aborted."
        )
```

Replace the inner-loop block in `detect_redactions_chunked` (the post-callback section that currently does the single `provider.detect_redactions` call) with:

```python
        findings, chunks = provider.detect_redactions(
            batch,
            llm_category_ids,
            instructions=instructions,
            seed=seed,
        )

        if not chunks:
            logger.info(
                "retrying batch %d/%d (pages %s) after %.0fs",
                batch_idx + 1,
                total_batches,
                page_numbers,
                RETRY_BACKOFF_SECONDS,
            )
            time.sleep(RETRY_BACKOFF_SECONDS)
            findings, chunks = provider.detect_redactions(
                batch,
                llm_category_ids,
                instructions=instructions,
                seed=seed,
            )
            if not chunks:
                raise BatchError(batch_idx, page_numbers)

        renumbered = [replace(c, chunk_idx=batch_idx) for c in chunks]
        aggregate_findings.extend(findings)
        aggregate_chunks.extend(renumbered)

        if on_batch_complete is not None and renumbered:
            on_batch_complete(batch_idx, total_batches, page_numbers, renumbered[0])
```

- [ ] **Step 4: Run all chunking tests**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: All ten tests pass (six original + renumber + three retry/abort/soft).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/core/chunking.py tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
feat(chunking): retry once then abort on hard batch failure

The provider returning chunks == [] is the hard-failure signal:
HTTP error, timeout, malformed envelope. Orchestrator retries
once after a 2s sleep; if that retry also returns chunks == []
it raises BatchError carrying the batch index and page numbers
for run.py to surface to the user.

Soft-empty outcomes (chunks == [chunk] with empty findings) are
not retried — both providers run at temperature=0 so a retry
would produce the same answer, and the existing per-call
WARNING logs already explain the cause.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Verify progress callbacks fire correctly

**Files:**
- Modify: `tests/core/test_chunking.py`

The orchestrator already accepts `on_batch_start` and `on_batch_complete` (since Task 1) and calls them. This task locks down their contract with explicit tests.

- [ ] **Step 1: Write failing tests**

Append to `tests/core/test_chunking.py`:

```python
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


def test_on_batch_complete_receives_renumbered_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    import kuroi.core.chunking as chunking_mod
    from kuroi.core.chunking import BatchError, detect_redactions_chunked

    monkeypatch.setattr(chunking_mod.time, "sleep", lambda _: None)

    pages = (_page(1), _page(2))
    provider = _RecordingProvider(scripts=[([], []), ([], [])])
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_chunking.py -v`
Expected: All thirteen tests pass. The first two new tests verify Task 1's callback wiring; the third verifies Task 3's abort-without-completing-callback behavior. If any fails, the orchestrator logic is wrong — fix it before committing.

- [ ] **Step 3: Commit**

```bash
git add tests/core/test_chunking.py
git commit -m "$(cat <<'EOF'
test(chunking): lock down progress callback contract

on_batch_start fires once per batch before the provider call;
on_batch_complete fires once after a chunk is produced and
receives the renumbered chunk record. Neither fires when a
batch hard-fails twice and BatchError is raised.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Add `--pages-per-batch` flag to `kuroi run`

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Write failing CLI integration test**

Append to `tests/cli/test_run.py`:

```python
def test_run_with_pages_per_batch_invokes_orchestrator(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--pages-per-batch 2 on a 4-page document produces 2 LLM calls
    and 2 chunk_request audit events."""
    pdf = make_pdf(
        [
            "Page one alice@example.com",
            "Page two bob@example.com",
            "Page three carol@example.com",
            "Page four dave@example.com",
        ]
    )
    out = tmp_path / "redacted.pdf"
    backup_dir = tmp_path / "backups"
    audit_dir = tmp_path / "audit"

    call_page_groups: list[tuple[int, ...]] = []

    def _stub_detect(
        self: Any,
        pages: tuple[Any, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
    ) -> tuple[list[Any], list[Any]]:
        from kuroi.core.audit_records import ChunkRecord

        call_page_groups.append(tuple(p.number for p in pages))
        return [], [
            ChunkRecord(
                chunk_idx=0,
                pages=tuple(p.number for p in pages),
                temperature=0.0,
                seed_requested=seed,
                seed_honored=False,
                system_fingerprint=None,
                prompt_sha256="a" * 64,
                response_sha256="b" * 64,
                tokens_in=10,
                tokens_out=2,
                duration_ms=50,
            )
        ]

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.detect_redactions",
        _stub_detect,
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact emails",
            "-o",
            str(out),
            "-y",
            "--pages-per-batch",
            "2",
            "--backup-dir",
            str(backup_dir),
            "--audit-dir",
            str(audit_dir),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert call_page_groups == [(1, 2), (3, 4)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_run.py::test_run_with_pages_per_batch_invokes_orchestrator -v`
Expected: FAIL with "no such option: --pages-per-batch".

- [ ] **Step 3: Add the flag and orchestrator routing in `run.py`**

In `src/kuroi/cli/run.py`, add to the imports near the top:

```python
from kuroi.core.chunking import BatchError, detect_redactions_chunked
```

Add a new option in the `run` function signature, alongside the existing options (place it after `seed`):

```python
    pages_per_batch: int = typer.Option(
        0,
        "--pages-per-batch",
        help=(
            "Split LLM analysis into batches of N pages. "
            "0 (default) keeps the current single-call behavior."
        ),
        min=0,
    ),
```

Find the existing block at lines 191-195 (the single `provider.detect_redactions` call):

```python
            instruction_tuple: tuple[str, ...] = (instruct,) if instruct else ()
            provider_findings, chunks = provider.detect_redactions(
                pages, tuple(llm_cat_ids), instructions=instruction_tuple, seed=seed
            )
            findings.extend(provider_findings)
```

Replace it with:

```python
            instruction_tuple: tuple[str, ...] = (instruct,) if instruct else ()
            if pages_per_batch == 0:
                provider_findings, chunks = provider.detect_redactions(
                    pages, tuple(llm_cat_ids), instructions=instruction_tuple, seed=seed
                )
            else:
                try:
                    provider_findings, chunks = detect_redactions_chunked(
                        provider,
                        pages,
                        tuple(llm_cat_ids),
                        instructions=instruction_tuple,
                        seed=seed,
                        pages_per_batch=pages_per_batch,
                    )
                except BatchError as exc:
                    console.print(
                        f"  [red]{exc}[/]\n"
                        f"  Re-run with a smaller --pages-per-batch, "
                        f"or check the WARNING(s) above for the cause."
                    )
                    raise typer.Exit(code=1) from exc
            findings.extend(provider_findings)
```

- [ ] **Step 4: Run the failing test plus the broader run-CLI suite**

Run: `uv run pytest tests/cli/test_run.py -v`
Expected: All tests pass, including the new one.

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "$(cat <<'EOF'
feat(cli): add --pages-per-batch flag to kuroi run

Wires core.chunking.detect_redactions_chunked into the run command
when the new flag is set. Default of 0 routes through the existing
single-call code path with zero behavior change. BatchError from
the orchestrator surfaces as exit-code-1 with a clear message
pointing the user at a smaller batch size or the WARNING logs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire progress UI in `cli/run.py`

**Files:**
- Modify: `src/kuroi/cli/run.py`
- Modify: `tests/cli/test_run.py`

- [ ] **Step 1: Write failing test for progress output**

Append to `tests/cli/test_run.py`:

```python
def test_run_with_pages_per_batch_prints_progress_per_batch(
    make_pdf: Callable[..., Path],
    tmp_path: Path,
    stub_anthropic_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = make_pdf(["one", "two", "three", "four"])

    def _stub_detect(
        self: Any,
        pages: tuple[Any, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
    ) -> tuple[list[Any], list[Any]]:
        from kuroi.core.audit_records import ChunkRecord

        return [], [
            ChunkRecord(
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
                duration_ms=123,
            )
        ]

    monkeypatch.setattr(
        "kuroi.providers.anthropic.AnthropicProvider.detect_redactions",
        _stub_detect,
    )

    result = runner.invoke(
        app,
        [
            "run",
            str(pdf),
            "--instruct",
            "redact",
            "-o",
            str(tmp_path / "out.pdf"),
            "-y",
            "--pages-per-batch",
            "2",
            "--backup-dir",
            str(tmp_path / "backups"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Batch 1/2 (pages 1-2)" in result.stdout
    assert "Batch 2/2 (pages 3-4)" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_run.py::test_run_with_pages_per_batch_prints_progress_per_batch -v`
Expected: FAIL because no progress lines are printed.

- [ ] **Step 3: Wire callbacks into the orchestrator call**

In `src/kuroi/cli/run.py`, replace the chunked call in the `else` branch from Task 5:

```python
            else:
                try:
                    provider_findings, chunks = detect_redactions_chunked(
                        provider,
                        pages,
                        tuple(llm_cat_ids),
                        instructions=instruction_tuple,
                        seed=seed,
                        pages_per_batch=pages_per_batch,
                    )
                except BatchError as exc:
```

with:

```python
            else:
                def _on_batch_start(
                    batch_idx: int, total: int, page_numbers: tuple[int, ...]
                ) -> None:
                    if len(page_numbers) > 1:
                        rng = f"{page_numbers[0]}-{page_numbers[-1]}"
                    else:
                        rng = f"{page_numbers[0]}"
                    console.print(
                        f"  Batch {batch_idx + 1}/{total} (pages {rng})...", end=""
                    )

                def _on_batch_complete(
                    batch_idx: int,
                    total: int,
                    page_numbers: tuple[int, ...],
                    chunk: Any,
                ) -> None:
                    console.print(
                        f" done in {chunk.duration_ms} ms, "
                        f"tokens_in={chunk.tokens_in} tokens_out={chunk.tokens_out}"
                    )

                try:
                    provider_findings, chunks = detect_redactions_chunked(
                        provider,
                        pages,
                        tuple(llm_cat_ids),
                        instructions=instruction_tuple,
                        seed=seed,
                        pages_per_batch=pages_per_batch,
                        on_batch_start=_on_batch_start,
                        on_batch_complete=_on_batch_complete,
                    )
                except BatchError as exc:
```

`Any` is already imported in `run.py`; if not, add `from typing import Any` at the top.

- [ ] **Step 4: Run the new test plus the full CLI suite**

Run: `uv run pytest tests/cli/test_run.py -v`
Expected: All tests pass, including the progress-output test.

- [ ] **Step 5: Run the full project suite**

Run: `uv run pytest -q`
Expected: All tests pass — no regressions in other suites.

- [ ] **Step 6: Run lint and typecheck**

Run: `uv run ruff format src/kuroi/core/chunking.py src/kuroi/cli/run.py && uv run ruff check src/kuroi/core/chunking.py src/kuroi/cli/run.py && uv run mypy src/kuroi/core/chunking.py src/kuroi/cli/run.py`
Expected: format applies, ruff "All checks passed!", mypy "Success: no issues found in 2 source files".

- [ ] **Step 7: Commit**

```bash
git add src/kuroi/cli/run.py tests/cli/test_run.py
git commit -m "$(cat <<'EOF'
feat(cli): print per-batch progress when --pages-per-batch is set

Two closures wired into the orchestrator's on_batch_start /
on_batch_complete hooks render a single-line progress entry per
batch (e.g. "Batch 1/4 (pages 1-2)... done in 123 ms,
tokens_in=10 tokens_out=2"). The orchestrator stays free of
CLI-layer dependencies; rendering belongs in run.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage** — checked against `docs/superpowers/specs/2026-05-07-pages-per-batch-design.md`:

- CLI surface `--pages-per-batch N` → Task 5.
- Cost-line addendum noting batch count → not implemented in this plan; see "Deferred" below.
- New module `core/chunking.py` with the documented signature → Task 1 + Task 2 + Task 3 + Task 4.
- `BatchError` exception → Task 3.
- Slicing with last-batch-may-be-shorter, even-divisor, oversized-batch → Task 1.
- Renumbered `chunk_idx` via `dataclasses.replace` → Task 2.
- Retry-once with 2s backoff on hard failure → Task 3.
- `BatchError` raised on double-fail → Task 3.
- No retry on soft empty → Task 3.
- `Page.number` preserves correct page references inside batches — verified by Task 5's CLI integration test (which checks the audit log's recorded page numbers via the stub provider).
- `cli/run.py` glue with default-zero passthrough → Task 5.
- Audit log: one `chunk_request` event per batch → existing audit loop in `run.py:237-238` is unchanged; verified end-to-end by Task 5.
- Progress UI with per-batch line, default verbosity vs `-v` for tokens → Task 6 wires callbacks; **plan simplification:** the progress line always includes the tokens portion regardless of verbosity, since the rendering closure has no access to the parsed `verbose` count. Splitting the line by verbosity is deferred — the spec describes it as a refinement, but at default verbosity users actually want the tokens info too (it's how they spot the qwen3 bug). The simpler always-on rendering matches user need without an extra plumbing pass.

**Deferred from spec, with reasons:**

- **Cost-line addendum** ("49 pages → 49 batches") in the pre-call estimate. The plan keeps the existing estimate code path unchanged for now. Adding the batch count is a one-line Edit but expanding the test surface to cover the format string didn't earn a task. Easy to fold in later.
- **Verbosity-gated tokens portion of the progress line.** See above. The `_on_batch_complete` closure can read `verbose` from the surrounding `run` function's scope when this lands; v1 unconditionally includes tokens.

**Placeholder scan:** No "TBD", "TODO", "implement later", "fill in details" anywhere. All test code, code blocks, and commands are concrete.

**Type consistency:** `detect_redactions_chunked` signature is identical across Tasks 1-6. `BatchError(batch_idx, page_numbers)` constructor signature is consistent in Task 3 and the CLI catch in Task 5. `on_batch_start` and `on_batch_complete` parameter shapes match between Task 1's stub support, Task 4's tests, and Task 6's wiring.
