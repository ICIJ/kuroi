"""Tests for OllamaProvider — exercise the HTTP path with a stub client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, Word
from kuroi.providers.ollama import OllamaProvider


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


class _StubResponse:
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=httpx.Request("POST", "http://x"), response=self  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return json.loads(self._body)

    @property
    def text(self) -> str:
        return self._body


class _StubClient:
    """Minimal stand-in for httpx.Client.post()."""

    def __init__(
        self,
        *,
        response: _StubResponse | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.last_url: str | None = None
        self.last_json: dict[str, Any] | None = None
        self.call_count = 0

    def post(self, url: str, *, json: dict[str, Any], timeout: Any = None) -> _StubResponse:
        self.call_count += 1
        self.last_url = url
        self.last_json = json
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.response is not None
        return self.response


def _ok_response(content: str) -> _StubResponse:
    body = json.dumps({"message": {"role": "assistant", "content": content}, "done": True})
    return _StubResponse(status_code=200, body=body)


def test_detect_redactions_round_trips_through_stub_client() -> None:
    findings_json = (
        '{"findings": [{"page": 1, "start": 1, "end": 2, '
        '"kind": "person_name", "confidence": "high"}]}'
    )
    client = _StubClient(response=_ok_response(findings_json))
    provider = OllamaProvider(
        model="llama3.1:8b",
        url="http://localhost:11434",
        client=client,  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)

    findings = provider.detect_redactions(pages, llm_category_ids=("person_name",))

    assert len(findings) == 1
    assert isinstance(findings[0], Finding)
    assert findings[0].kind == "person_name"
    assert findings[0].source == "llm"
    assert client.last_url == "http://localhost:11434/api/chat"
    assert client.last_json is not None
    assert client.last_json["model"] == "llama3.1:8b"
    assert client.last_json["format"] == "json"
    assert client.last_json["stream"] is False
    msgs = client.last_json["messages"]
    assert msgs[0]["role"] == "system"
    assert "kuroi" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert "<document>" in msgs[1]["content"]


def test_detect_redactions_short_circuits_with_no_categories() -> None:
    client = _StubClient(response=_ok_response('{"findings": []}'))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)

    findings = provider.detect_redactions(pages, llm_category_ids=())

    assert findings == []
    assert client.call_count == 0


def test_connect_error_returns_empty_findings() -> None:
    client = _StubClient(raise_exc=httpx.ConnectError("daemon down"))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    assert provider.detect_redactions(pages, ("person_name",)) == []


def test_timeout_returns_empty_findings() -> None:
    client = _StubClient(raise_exc=httpx.TimeoutException("slow"))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    assert provider.detect_redactions(pages, ("person_name",)) == []


def test_500_response_returns_empty_findings() -> None:
    client = _StubClient(response=_StubResponse(status_code=500, body=""))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    assert provider.detect_redactions(pages, ("person_name",)) == []


def test_non_json_body_returns_empty_findings() -> None:
    client = _StubClient(response=_ok_response("this is not json"))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)
    assert provider.detect_redactions(pages, ("person_name",)) == []


def test_missing_message_content_returns_empty_findings() -> None:
    body = json.dumps({"done": True})
    client = _StubClient(response=_StubResponse(status_code=200, body=body))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    assert provider.detect_redactions(pages, ("person_name",)) == []


def test_strips_trailing_url_slash() -> None:
    client = _StubClient(response=_ok_response('{"findings": []}'))
    provider = OllamaProvider(
        model="m", url="http://localhost:11434/", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    provider.detect_redactions(pages, ("person_name",))
    assert client.last_url == "http://localhost:11434/api/chat"
