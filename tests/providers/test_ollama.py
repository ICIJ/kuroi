"""Tests for OllamaProvider — exercise the HTTP path with a stub client."""

from __future__ import annotations

import json
import json as _json_module
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

    findings, _chunks = provider.detect_redactions(pages, llm_category_ids=("person_name",))

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

    findings, chunks = provider.detect_redactions(pages, llm_category_ids=())

    assert findings == []
    assert chunks == []
    assert client.call_count == 0


def test_connect_error_returns_empty_findings() -> None:
    client = _StubClient(raise_exc=httpx.ConnectError("daemon down"))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    findings, chunks = provider.detect_redactions(pages, ("person_name",))
    assert findings == []
    assert chunks == []


def test_timeout_returns_empty_findings() -> None:
    client = _StubClient(raise_exc=httpx.TimeoutException("slow"))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    findings, chunks = provider.detect_redactions(pages, ("person_name",))
    assert findings == []
    assert chunks == []


def test_500_response_returns_empty_findings() -> None:
    client = _StubClient(response=_StubResponse(status_code=500, body=""))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    findings, chunks = provider.detect_redactions(pages, ("person_name",))
    assert findings == []
    assert chunks == []


def test_non_json_body_returns_empty_findings() -> None:
    client = _StubClient(response=_ok_response("this is not json"))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello", "Sarah", "Chen"]),)
    findings, _chunks = provider.detect_redactions(pages, ("person_name",))
    assert findings == []


def test_missing_message_content_returns_empty_findings() -> None:
    body = json.dumps({"done": True})
    client = _StubClient(response=_StubResponse(status_code=200, body=body))
    provider = OllamaProvider(
        model="llama3.1:8b", url="http://localhost:11434", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    findings, _chunks = provider.detect_redactions(pages, ("person_name",))
    assert findings == []


def test_strips_trailing_url_slash() -> None:
    client = _StubClient(response=_ok_response('{"findings": []}'))
    provider = OllamaProvider(
        model="m", url="http://localhost:11434/", client=client  # type: ignore[arg-type]
    )
    pages = (_page(1, ["Hello"]),)
    provider.detect_redactions(pages, ("person_name",))
    assert client.last_url == "http://localhost:11434/api/chat"


class _StubClient2:
    def __init__(
        self, response_body: dict[str, Any], prompt_eval: int = 100, eval_count: int = 20
    ) -> None:
        self.response_body = response_body
        self.prompt_eval = prompt_eval
        self.eval_count = eval_count
        self.last_call_kwargs: dict[str, Any] | None = None

    def post(self, url: str, *, json: dict[str, Any]) -> Any:
        self.last_call_kwargs = {"url": url, "json": json}
        envelope = {
            "message": {"content": _json_module.dumps(self.response_body)},
            "prompt_eval_count": self.prompt_eval,
            "eval_count": self.eval_count,
        }

        class R:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> Any:
                return envelope

        return R()


def _one_page() -> tuple[Page, ...]:
    return (
        Page(
            number=1,
            words=(Word(idx=0, text="Sarah", bbox=(0.0, 0.0, 10.0, 10.0)),),
        ),
    )


def test_ollama_returns_findings_and_chunk_record() -> None:
    client = _StubClient2({"findings": []}, prompt_eval=42, eval_count=7)
    provider = OllamaProvider(model="llama3.1:70b", url="http://x", client=client)

    findings, chunks = provider.detect_redactions(_one_page(), ("person_name",))

    assert findings == []
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.tokens_in == 42
    assert chunk.tokens_out == 7
    assert chunk.seed_honored is False  # no seed requested
    assert chunk.seed_requested is None


def test_ollama_seed_is_sent_and_marked_honored() -> None:
    client = _StubClient2({"findings": []})
    provider = OllamaProvider(model="llama3.1:70b", url="http://x", client=client)

    _, chunks = provider.detect_redactions(_one_page(), ("person_name",), seed=99)

    assert chunks[0].seed_requested == 99
    assert chunks[0].seed_honored is True
    body = client.last_call_kwargs["json"]
    assert body["options"]["seed"] == 99
    assert body["options"]["temperature"] == 0


def test_ollama_instructions_only_makes_call_and_sets_source() -> None:
    """Ollama calls the model when instructions are given and no categories."""
    client = _StubClient2(
        {
            "findings": [
                {"page": 1, "start": 0, "end": 0, "kind": "ip_address", "confidence": "high"}
            ]
        },
        prompt_eval=50,
        eval_count=10,
    )
    provider = OllamaProvider(model="llama3.1:8b", url="http://x", client=client)
    pages = (_page(1, ["192.168.1.1"]),)

    findings, chunks = provider.detect_redactions(
        pages, llm_category_ids=(), instructions=("redact all IP addresses",)
    )

    assert len(findings) == 1
    assert findings[0].source == "instruction"
    assert findings[0].kind == "ip_address"
    assert client.last_call_kwargs is not None
    user_msg = client.last_call_kwargs["json"]["messages"][1]["content"]
    assert "redact all IP addresses" in user_msg
    assert "Active LLM categories" not in user_msg


def test_ollama_mixed_source_is_llm() -> None:
    """When both categories and instructions are given, source is 'llm'."""
    client = _StubClient2(
        {"findings": [{"page": 1, "start": 0, "end": 0, "kind": "email", "confidence": "high"}]},
    )
    provider = OllamaProvider(model="llama3.1:8b", url="http://x", client=client)
    pages = (_page(1, ["alice@example.com"]),)

    findings, _ = provider.detect_redactions(
        pages, llm_category_ids=("email",), instructions=("also redact names",)
    )

    assert findings[0].source == "llm"
