"""Tests for audit-log replay parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kuroi.core.audit_replay import (
    ReplayableFinding,
    ReplayableSession,
    load_session,
)


def _write_log(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_load_session_parses_session_start_and_applied_findings(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "session_start",
                "session_id": "abc-123",
                "input_path": "/work/in.pdf",
                "output_path": "/work/out.pdf",
            },
            {
                "event": "finding",
                "page": 3,
                "word_start": 12,
                "word_end": 14,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": [10.0, 20.0, 90.0, 30.0],
                "decision": "applied",
            },
            {
                "event": "finding",
                "page": 5,
                "word_start": 2,
                "word_end": 2,
                "kind": "person",
                "confidence": "medium",
                "source": "rules:pii-en",
                "bbox": None,
                "decision": "applied",
            },
            {"event": "session_end", "status": "ok"},
        ],
    )

    session = load_session(log)

    assert isinstance(session, ReplayableSession)
    assert session.session_id == "abc-123"
    assert session.input_path == Path("/work/in.pdf")
    assert session.output_path == Path("/work/out.pdf")
    assert len(session.findings) == 2
    assert session.findings[0] == ReplayableFinding(
        page=3,
        word_start=12,
        word_end=14,
        kind="email",
        confidence="high",
        source="llm",
        bbox=(10.0, 20.0, 90.0, 30.0),
    )
    assert session.findings[1].bbox is None


def test_load_session_skips_non_applied_decisions(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "session_start",
                "session_id": "x",
                "input_path": "/i",
                "output_path": "/o",
            },
            {
                "event": "finding",
                "page": 1,
                "word_start": 0,
                "word_end": 0,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": None,
                "decision": "applied",
            },
            {
                "event": "finding",
                "page": 1,
                "word_start": 5,
                "word_end": 5,
                "kind": "phone",
                "confidence": "low",
                "source": "llm",
                "bbox": None,
                "decision": "rejected",
            },
        ],
    )

    session = load_session(log)
    assert len(session.findings) == 1
    assert session.findings[0].kind == "email"


def test_load_session_tolerates_unknown_fields(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "session_start",
                "session_id": "x",
                "input_path": "/i",
                "output_path": "/o",
                "future_field": 42,
            },
            {
                "event": "finding",
                "page": 1,
                "word_start": 0,
                "word_end": 0,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": None,
                "decision": "applied",
                "unexpected": "ignore me",
            },
            {"event": "some_future_event", "payload": True},
        ],
    )

    session = load_session(log)
    assert session.session_id == "x"
    assert len(session.findings) == 1


def test_load_session_missing_session_start_raises(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    _write_log(
        log,
        [
            {
                "event": "finding",
                "page": 1,
                "word_start": 0,
                "word_end": 0,
                "kind": "email",
                "confidence": "high",
                "source": "llm",
                "bbox": None,
                "decision": "applied",
            }
        ],
    )

    with pytest.raises(ValueError, match="session_start"):
        load_session(log)


def test_load_session_malformed_line_raises(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    log.write_text(
        '{"event": "session_start", "session_id": "x", "input_path": "/i", "output_path": "/o"}\n{not json\n'
    )
    with pytest.raises(ValueError, match="line 2"):
        load_session(log)


from kuroi.core.audit_replay import build_exclusion_set


def _make_findings() -> tuple[ReplayableFinding, ...]:
    return (
        ReplayableFinding(3, 12, 14, "email", "high", "llm", None),       # idx 0
        ReplayableFinding(3, 20, 21, "person", "medium", "rules", None),  # idx 1
        ReplayableFinding(5, 0, 2, "person", "high", "llm", None),        # idx 2
        ReplayableFinding(5, 8, 8, "date", "low", "llm", None),           # idx 3
        ReplayableFinding(7, 4, 6, "email", "high", "llm", None),         # idx 4
    )


def test_build_exclusion_set_by_pages() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=(3, 5),
        page=None,
        kind=None,
        words=None,
        picker_indices=(),
    )
    assert result == frozenset({0, 1, 2, 3})


def test_build_exclusion_set_by_page_and_kind() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=3,
        kind="email",
        words=None,
        picker_indices=(),
    )
    assert result == frozenset({0})


def test_build_exclusion_set_by_page_and_words_exact_range() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=5,
        kind=None,
        words=(0, 2),
        picker_indices=(),
    )
    assert result == frozenset({2})


def test_build_exclusion_set_empty_when_filters_match_nothing() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=3,
        kind="iban",
        words=None,
        picker_indices=(),
    )
    assert result == frozenset()


def test_build_exclusion_set_picker_indices_union() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=3,
        kind="email",
        words=None,
        picker_indices=(2, 4),
    )
    assert result == frozenset({0, 2, 4})


def test_build_exclusion_set_no_filters_returns_empty() -> None:
    findings = _make_findings()
    result = build_exclusion_set(
        findings,
        pages=None,
        page=None,
        kind=None,
        words=None,
        picker_indices=(),
    )
    assert result == frozenset()
