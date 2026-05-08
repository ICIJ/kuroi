import logging
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, Word
from kuroi.providers.anthropic import (
    AnthropicProvider,
    build_user_prompt,
    parse_findings_payload,
)


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_build_user_prompt_wraps_document_in_tag() -> None:
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)
    prompt = build_user_prompt(pages, llm_category_ids=("person_name",))

    assert "<document>" in prompt
    assert "</document>" in prompt
    # Word-index markers visible to the model
    assert "[0]Hello" in prompt
    assert "[1]Sarah" in prompt
    # Active LLM categories listed
    assert "person_name" in prompt


def test_parse_findings_payload_returns_findings() -> None:
    payload = {
        "findings": [
            {"page": 1, "start": 1, "end": 2, "kind": "person_name", "confidence": "high"},
            {"page": 1, "start": 0, "end": 0, "kind": "email", "confidence": "medium"},
        ]
    }
    pages = (_page(1, ["alice@example.com", "Sarah", "Chen"]),)
    findings = parse_findings_payload(payload, pages, source="llm")

    assert len(findings) == 2
    assert all(isinstance(f, Finding) for f in findings)
    assert findings[0].kind == "person_name"
    assert findings[0].source == "llm"


def test_parse_findings_payload_drops_out_of_range() -> None:
    payload = {
        "findings": [
            {"page": 1, "start": 99, "end": 100, "kind": "person_name", "confidence": "high"},
            {"page": 7, "start": 0, "end": 0, "kind": "person_name", "confidence": "high"},
        ]
    }
    pages = (_page(1, ["alice", "bob"]),)
    findings = parse_findings_payload(payload, pages, source="llm")
    assert findings == []


@dataclass
class _StubBlock:
    text: str


@dataclass
class _StubResponse:
    content: list[_StubBlock]


class _StubMessages:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.last_call: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _StubResponse:
        self.last_call = kwargs
        return _StubResponse(content=[_StubBlock(text=self._response_text)])


class _StubClient:
    def __init__(self, response_text: str) -> None:
        self.messages = _StubMessages(response_text)


def test_detect_redactions_round_trips_through_stub_client() -> None:
    response_text = (
        '{"findings": [{"page": 1, "start": 1, "end": 2, '
        '"kind": "person_name", "confidence": "high"}]}'
    )
    client = _StubClient(response_text)
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)

    findings, _chunks = provider.detect_redactions(pages, llm_category_ids=("person_name",))

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "person_name"
    assert f.start == 1 and f.end == 2
    # The system prompt was applied
    assert "kuroi" in client.messages.last_call["system"]
    # The user prompt contained the document tag
    user_msg = client.messages.last_call["messages"][0]["content"]
    assert "<document>" in user_msg


def test_detect_redactions_short_circuits_with_no_categories() -> None:
    client = _StubClient('{"findings": []}')
    provider = AnthropicProvider(client=client)
    pages = (_page(1, ["Hello"]),)

    findings, chunks = provider.detect_redactions(pages, llm_category_ids=())

    assert findings == []
    assert chunks == []
    # No call was made because there were no LLM categories.
    assert client.messages.last_call is None


def _stub_response(
    text: str, fingerprint: str | None = None, in_t: int = 100, out_t: int = 20
) -> MagicMock:
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=in_t, output_tokens=out_t)
    response.system_fingerprint = fingerprint
    return response


def _one_page() -> tuple[Page, ...]:
    return (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Sarah", bbox=(0.0, 0.0, 10.0, 10.0)),
                Word(idx=1, text="Chen", bbox=(11.0, 0.0, 20.0, 10.0)),
            ),
        ),
    )


def test_anthropic_returns_findings_and_chunk_record() -> None:
    client = MagicMock()
    client.messages.create.return_value = _stub_response(
        '{"findings": [{"page": 1, "start": 0, "end": 1, "kind": "person_name", "confidence": "high"}]}',
        fingerprint=None,
        in_t=100,
        out_t=20,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert len(findings) == 1
    assert findings[0].kind == "person_name"
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_idx == 0
    assert chunk.pages == (1,)
    assert chunk.temperature == 0.0
    assert chunk.seed_requested is None
    assert chunk.seed_honored is False  # Anthropic SDK never honors a seed
    assert chunk.tokens_in == 100
    assert chunk.tokens_out == 20
    assert len(chunk.prompt_sha256) == 64
    assert len(chunk.response_sha256) == 64


def test_anthropic_records_seed_but_does_not_send_it() -> None:
    client = MagicMock()
    client.messages.create.return_value = _stub_response(
        '{"findings": []}', in_t=10, out_t=2
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    _, chunks = provider.detect_redactions(_one_page(), ("person_name",), seed=42)

    # seed is recorded, never sent.
    assert chunks[0].seed_requested == 42
    assert chunks[0].seed_honored is False
    call_kwargs = client.messages.create.call_args.kwargs
    assert "seed" not in call_kwargs
    # claude-opus-4-7 rejects temperature; it must not be sent
    assert "temperature" not in call_kwargs


def test_anthropic_no_categories_returns_empty_lists() -> None:
    client = MagicMock()
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)
    findings, chunks = provider.detect_redactions(_one_page(), ())
    assert findings == []
    assert chunks == []
    client.messages.create.assert_not_called()


def test_detect_redactions_with_instructions_only_makes_llm_call() -> None:
    """Provider makes an LLM call when instructions are given even with no categories."""
    response_text = (
        '{"findings": [{"page": 1, "start": 0, "end": 1, '
        '"kind": "complainant_name", "confidence": "high"}]}'
    )
    client = _StubClient(response_text)
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)
    pages = (_page(1, ["Alice", "Smith"]),)

    findings, _ = provider.detect_redactions(
        pages, llm_category_ids=(), instructions=("redact all complainant names",)
    )

    assert len(findings) == 1
    assert findings[0].source == "instruction"
    assert findings[0].kind == "complainant_name"
    assert client.messages.last_call is not None
    user_msg = client.messages.last_call["messages"][0]["content"]
    assert "redact all complainant names" in user_msg
    assert "Active LLM categories" not in user_msg


def test_detect_redactions_mixed_source_is_llm() -> None:
    """When both categories and instructions given, source is 'llm'."""
    response_text = (
        '{"findings": [{"page": 1, "start": 0, "end": 0, '
        '"kind": "email", "confidence": "high"}]}'
    )
    client = _StubClient(response_text)
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)
    pages = (_page(1, ["alice@example.com"]),)

    findings, _ = provider.detect_redactions(
        pages,
        llm_category_ids=("email",),
        instructions=("also redact all names",),
    )

    assert findings[0].source == "llm"


def test_anthropic_logs_full_prompt_and_response_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """-vv must surface the full prompt and response so users can diagnose
    bad/empty findings without re-running with extra instrumentation."""
    response_text = '{"findings": []}'
    client = _StubClient(response_text)
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)

    with caplog.at_level(logging.DEBUG, logger="kuroi.providers.anthropic"):
        provider.detect_redactions(pages, llm_category_ids=("person_name",))

    debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("<document>" in m for m in debug_msgs), "full prompt should appear at DEBUG"
    assert any(response_text in m for m in debug_msgs), "full response should appear at DEBUG"


def test_anthropic_logs_token_and_duration_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """-v must surface tokens_in/tokens_out so the user can compare against
    the local estimate and spot context-window truncation."""
    client = MagicMock()
    client.messages.create.return_value = _stub_response(
        '{"findings": []}', in_t=164411, out_t=12
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    with caplog.at_level(logging.INFO, logger="kuroi.providers.anthropic"):
        provider.detect_redactions(_one_page(), ("person_name",))

    info_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("tokens_in=164411" in m and "tokens_out=12" in m for m in info_msgs)


def test_anthropic_warns_when_response_likely_truncated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When tokens_out approaches max_tokens, the response was almost certainly
    cut mid-JSON — the user must see this even at default verbosity."""
    client = MagicMock()
    client.messages.create.return_value = _stub_response(
        '{"findings": [', in_t=1000, out_t=4096  # truncated payload, hit max_tokens
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client, max_tokens=4096)

    with caplog.at_level(logging.WARNING, logger="kuroi.providers.anthropic"):
        findings, _ = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("truncat" in m.lower() and "max_tokens" in m for m in warnings)


def test_anthropic_warns_on_non_json_response(caplog: pytest.LogCaptureFixture) -> None:
    """When the model returns a markdown fence or preamble instead of pure JSON,
    the user must see a WARNING — not silently get zero findings."""
    client = _StubClient("Sure, here are the redactions:\n```json\n{}\n```")
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    with caplog.at_level(logging.WARNING, logger="kuroi.providers.anthropic"):
        findings, _ = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("non-json" in m.lower() or "json" in m.lower() for m in warnings)


def test_anthropic_prompt_too_long_returns_empty_to_signal_subdivide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 'prompt is too long' BadRequestError is caught and translated to
    the chunker's subdivide signal: empty findings AND empty chunks."""
    import anthropic

    client = MagicMock()
    err = anthropic.BadRequestError(
        message="prompt is too long: 250000 tokens > 200000 maximum",
        response=MagicMock(),
        body={
            "error": {
                "type": "invalid_request_error",
                "message": "prompt is too long: 250000 tokens > 200000 maximum",
            }
        },
    )
    client.messages.create.side_effect = err
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    with caplog.at_level(logging.WARNING, logger="kuroi.providers.anthropic"):
        findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("prompt" in m.lower() and "too long" in m.lower() for m in warnings)


def test_anthropic_truncated_response_returns_empty_to_signal_subdivide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the model hits ~95% of max_tokens, the JSON is almost certainly
    cut off. Today this is a silent data-loss path (returns [chunk] with
    zero findings); now it must return ([], []) so the chunker subdivides
    and recovers the lost findings on a smaller prompt."""
    client = MagicMock()
    # max_tokens defaults to 4096; tokens_out=4000 is ~97.7%.
    client.messages.create.return_value = _stub_response(
        '{"findings": [{"page": 1, "start": 0, "end":',  # truncated mid-array
        in_t=100,
        out_t=4000,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=client, max_tokens=4096)

    with caplog.at_level(logging.WARNING, logger="kuroi.providers.anthropic"):
        findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert chunks == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("truncat" in m.lower() for m in warnings)


def test_anthropic_other_bad_request_errors_still_raise() -> None:
    """Non-size BadRequestErrors (malformed schema, unknown model, etc.)
    are real bugs; subdivision won't help, so they keep propagating."""
    import anthropic

    client = MagicMock()
    err = anthropic.BadRequestError(
        message="model: unknown-model is not a recognized model",
        response=MagicMock(),
        body={
            "error": {
                "type": "invalid_request_error",
                "message": "model: unknown-model is not a recognized model",
            }
        },
    )
    client.messages.create.side_effect = err
    provider = AnthropicProvider(model="unknown-model", client=client)

    with pytest.raises(anthropic.BadRequestError):
        provider.detect_redactions(_one_page(), ("person_name",))


def test_anthropic_layout_aware_off_uses_plain_system_prompt() -> None:
    from kuroi.providers._shared import SYSTEM_PROMPT, LAYOUT_AWARE_INSTRUCTIONS

    pages = (_page(1, ["Hello", "world"]),)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"findings": []}')],
        usage=MagicMock(input_tokens=10, output_tokens=2),
        system_fingerprint=None,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=fake_client)

    provider.detect_redactions(pages, llm_category_ids=("k",))

    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["system"] == SYSTEM_PROMPT
    assert LAYOUT_AWARE_INSTRUCTIONS not in kwargs["system"]
    assert "<block" not in kwargs["messages"][0]["content"]


def test_anthropic_layout_aware_on_appends_paragraph_and_wraps_user_prompt() -> None:
    from kuroi.providers._shared import LAYOUT_AWARE_INSTRUCTIONS

    # Give both words distinct block_ids so layout-aware emits two block tags.
    pages = (
        Page(
            number=1,
            words=(
                Word(idx=0, text="Hello", bbox=(0, 0, 1, 1), block_id=1),
                Word(idx=1, text="world", bbox=(1, 0, 2, 1), block_id=2),
            ),
        ),
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"findings": []}')],
        usage=MagicMock(input_tokens=10, output_tokens=2),
        system_fingerprint=None,
    )
    provider = AnthropicProvider(model="claude-opus-4-7", client=fake_client)

    provider.detect_redactions(pages, llm_category_ids=("k",), layout_aware=True)

    kwargs = fake_client.messages.create.call_args.kwargs
    assert LAYOUT_AWARE_INSTRUCTIONS in kwargs["system"]
    user_content = kwargs["messages"][0]["content"]
    assert '<block id="1">[0]Hello</block>' in user_content
    assert '<block id="2">[1]world</block>' in user_content
