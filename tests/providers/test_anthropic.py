from dataclasses import dataclass
from typing import Any

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


import hashlib
from unittest.mock import MagicMock

from kuroi.providers.anthropic import AnthropicProvider as _AnthropicProvider2


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
    provider = _AnthropicProvider2(model="claude-opus-4-7", client=client)

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
    provider = _AnthropicProvider2(model="claude-opus-4-7", client=client)

    _, chunks = provider.detect_redactions(_one_page(), ("person_name",), seed=42)

    # seed is recorded, never sent.
    assert chunks[0].seed_requested == 42
    assert chunks[0].seed_honored is False
    call_kwargs = client.messages.create.call_args.kwargs
    assert "seed" not in call_kwargs
    assert call_kwargs["temperature"] == 0


def test_anthropic_no_categories_returns_empty_lists() -> None:
    client = MagicMock()
    provider = _AnthropicProvider2(model="claude-opus-4-7", client=client)
    findings, chunks = provider.detect_redactions(_one_page(), ())
    assert findings == []
    assert chunks == []
    client.messages.create.assert_not_called()
