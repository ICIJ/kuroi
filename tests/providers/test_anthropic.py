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

    findings = provider.detect_redactions(pages, llm_category_ids=("person_name",))

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

    findings = provider.detect_redactions(pages, llm_category_ids=())

    assert findings == []
    # No call was made because there were no LLM categories.
    assert client.messages.last_call is None
