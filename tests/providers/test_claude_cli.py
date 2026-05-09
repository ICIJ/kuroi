"""Tests for ClaudeCliProvider — async-bridged subprocess via claude-agent-sdk."""

from __future__ import annotations

from kuroi.core.pdf import Page, Word
from kuroi.providers.claude_cli import ClaudeCliProvider


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_provider_has_name_and_default_model() -> None:
    provider = ClaudeCliProvider()
    assert provider.name == "claude-cli"
    assert provider.model == "claude-opus-4-7"


def test_short_circuit_when_no_categories_and_no_instructions() -> None:
    provider = ClaudeCliProvider(query_fn=_must_not_be_called)
    pages = (_page(1, ["Hello"]),)
    findings, chunks = provider.detect_redactions(pages, llm_category_ids=())
    assert findings == []
    assert chunks == []


def _must_not_be_called(*args, **kwargs):
    raise AssertionError("query_fn should not be called when there is no work")


import json
from dataclasses import dataclass
from typing import Any


@dataclass
class _StubUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class _StubTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubAssistantMessage:
    def __init__(self, text: str) -> None:
        self.content = [_StubTextBlock(text)]


class _StubResultMessage:
    def __init__(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.usage = _StubUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _make_query_fn(messages: list[Any], captured: dict[str, Any] | None = None):
    """Build a stub `query` async-generator. Captures call args into `captured`."""

    async def stub(*, prompt: str, options: Any = None):
        if captured is not None:
            captured["prompt"] = prompt
            captured["options"] = options
        for m in messages:
            yield m

    return stub


def test_detect_redactions_round_trips_through_stub() -> None:
    findings_json = (
        '{"findings": [{"page": 1, "start": 1, "end": 2, '
        '"kind": "person_name", "confidence": "high"}]}'
    )
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage(findings_json),
            _StubResultMessage(input_tokens=1234, output_tokens=42),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-opus-4-7", query_fn=qfn)
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)

    findings, chunks = provider.detect_redactions(pages, llm_category_ids=("person_name",))

    assert len(findings) == 1
    assert findings[0].kind == "person_name"
    assert findings[0].source == "llm"
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.tokens_in == 1234
    assert chunk.tokens_out == 42
    assert chunk.pages == (1,)
    assert chunk.temperature == 0.0
    assert chunk.seed_honored is False
    assert chunk.system_fingerprint is None
    assert chunk.cache_creation_input_tokens == 0
    assert chunk.cache_read_input_tokens == 0
    # SHAs are deterministic for given inputs.
    assert len(chunk.prompt_sha256) == 64
    assert len(chunk.response_sha256) == 64
    # The prompt sent to the stub is static_prefix + document_block.
    assert "<document>" in captured["prompt"]
    assert "Active LLM categories: person_name" in captured["prompt"]


def test_per_call_model_override_replaces_provider_default() -> None:
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage('{"findings": []}'),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-opus-4-7", query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    provider.detect_redactions(
        pages, ("person_name",), model="claude-haiku-4-5-20251001"
    )

    options = captured["options"]
    assert options.model == "claude-haiku-4-5-20251001"


def test_provider_default_model_used_when_no_override() -> None:
    captured: dict[str, Any] = {}
    qfn = _make_query_fn(
        [
            _StubAssistantMessage('{"findings": []}'),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ],
        captured,
    )
    provider = ClaudeCliProvider(model="claude-sonnet-4-6", query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    provider.detect_redactions(pages, ("person_name",))

    options = captured["options"]
    assert options.model == "claude-sonnet-4-6"


def test_malformed_json_returns_empty_findings_with_chunk() -> None:
    qfn = _make_query_fn(
        [
            _StubAssistantMessage("this is not json"),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ]
    )
    provider = ClaudeCliProvider(query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    findings, chunks = provider.detect_redactions(pages, ("person_name",))

    assert findings == []
    assert len(chunks) == 1
    assert chunks[0].tokens_in == 10
    assert chunks[0].tokens_out == 2


def test_non_object_json_payload_returns_empty_findings_with_chunk() -> None:
    qfn = _make_query_fn(
        [
            _StubAssistantMessage("[1, 2, 3]"),
            _StubResultMessage(input_tokens=10, output_tokens=2),
        ]
    )
    provider = ClaudeCliProvider(query_fn=qfn)
    pages = (_page(1, ["Hello"]),)

    findings, chunks = provider.detect_redactions(pages, ("person_name",))

    assert findings == []
    assert len(chunks) == 1


def test_cli_not_found_raises_config_error() -> None:
    import pytest

    from kuroi.core.config import ConfigError

    class _FakeCLINotFound(Exception):
        pass

    async def qfn_raises(*, prompt: str, options: Any = None):
        raise _FakeCLINotFound("CLI binary not on PATH")
        yield  # pragma: no cover — make this an async generator

    provider = ClaudeCliProvider(query_fn=qfn_raises)
    pages = (_page(1, ["Hello"]),)

    # Patch the SDK's exception class so the provider catches our fake one.
    import kuroi.providers.claude_cli as mod

    monkeyed = {"orig": mod._cli_not_found_class}
    try:
        mod._cli_not_found_class = lambda: _FakeCLINotFound  # type: ignore[assignment]
        with pytest.raises(ConfigError, match="Claude CLI not found"):
            provider.detect_redactions(pages, ("person_name",))
    finally:
        mod._cli_not_found_class = monkeyed["orig"]  # type: ignore[assignment]


def test_process_error_with_auth_marker_raises_config_error() -> None:
    import pytest

    import kuroi.providers.claude_cli as mod
    from kuroi.core.config import ConfigError

    class _FakeProcessError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeProcessError("not authenticated: run claude /login")
        yield  # pragma: no cover

    orig = mod._process_error_class
    mod._process_error_class = lambda: _FakeProcessError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        with pytest.raises(ConfigError, match="claude /login"):
            provider.detect_redactions((_page(1, ["Hi"]),), ("person_name",))
    finally:
        mod._process_error_class = orig  # type: ignore[assignment]


def test_cli_json_decode_error_returns_empty_findings_with_chunk() -> None:
    import kuroi.providers.claude_cli as mod

    class _FakeJSONDecodeError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeJSONDecodeError("malformed envelope")
        yield  # pragma: no cover

    orig = mod._cli_json_decode_error_class
    mod._cli_json_decode_error_class = lambda: _FakeJSONDecodeError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        findings, chunks = provider.detect_redactions(
            (_page(1, ["Hi"]),), ("person_name",)
        )
        assert findings == []
        assert len(chunks) == 1
    finally:
        mod._cli_json_decode_error_class = orig  # type: ignore[assignment]


def test_cli_connection_error_returns_empty_findings_with_chunk() -> None:
    import kuroi.providers.claude_cli as mod

    class _FakeConnectionError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeConnectionError("transient")
        yield  # pragma: no cover

    orig = mod._cli_connection_error_class
    mod._cli_connection_error_class = lambda: _FakeConnectionError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        findings, chunks = provider.detect_redactions(
            (_page(1, ["Hi"]),), ("person_name",)
        )
        assert findings == []
        assert len(chunks) == 1
    finally:
        mod._cli_connection_error_class = orig  # type: ignore[assignment]


def test_process_error_without_auth_marker_returns_empty_findings() -> None:
    import kuroi.providers.claude_cli as mod

    class _FakeProcessError(Exception):
        pass

    async def qfn(*, prompt: str, options: Any = None):
        raise _FakeProcessError("the model timed out")
        yield  # pragma: no cover

    orig = mod._process_error_class
    mod._process_error_class = lambda: _FakeProcessError  # type: ignore[assignment]
    try:
        provider = ClaudeCliProvider(query_fn=qfn)
        findings, chunks = provider.detect_redactions(
            (_page(1, ["Hi"]),), ("person_name",)
        )
        assert findings == []
        assert len(chunks) == 1
    finally:
        mod._process_error_class = orig  # type: ignore[assignment]


def test_timeout_returns_empty_findings_with_chunk() -> None:
    import anyio

    async def qfn(*, prompt: str, options: Any = None):
        await anyio.sleep_forever()
        yield  # pragma: no cover

    provider = ClaudeCliProvider(query_fn=qfn, timeout_s=1)
    pages = (_page(1, ["Hi"]),)

    findings, chunks = provider.detect_redactions(pages, ("person_name",))

    assert findings == []
    assert len(chunks) == 1
