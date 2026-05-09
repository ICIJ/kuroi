"""Tests for Anthropic prompt caching: markers on the right blocks,
cache-token recording in ChunkRecord."""

from unittest.mock import MagicMock

from kuroi.core.pdf import Page, Word
from kuroi.providers.anthropic import AnthropicProvider


def _page() -> tuple[Page, ...]:
    return (Page(number=1, words=(Word(idx=0, text="Hello", bbox=(0, 0, 1, 1)),)),)


def _stub_response(text: str = '{"findings": []}', in_t: int = 100, out_t: int = 20):
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(
        input_tokens=in_t,
        output_tokens=out_t,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    response.system_fingerprint = None
    return response


def test_anthropic_request_uses_structured_blocks_with_cache_markers() -> None:
    client = MagicMock()
    client.messages.create.return_value = _stub_response()
    provider = AnthropicProvider(model="claude-opus-4-7", client=client)

    provider.detect_redactions(_page(), ("person_name",))

    kwargs = client.messages.create.call_args.kwargs

    # System: list of typed blocks with cache_control on the (single) block.
    assert isinstance(kwargs["system"], list)
    assert len(kwargs["system"]) == 1
    sys_block = kwargs["system"][0]
    assert sys_block["type"] == "text"
    assert sys_block["cache_control"] == {"type": "ephemeral"}

    # Messages: the user content is a list of two text blocks. Static prefix
    # carries cache_control; document block does not.
    assert kwargs["messages"][0]["role"] == "user"
    content = kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2

    static_block, doc_block = content
    assert static_block["type"] == "text"
    assert static_block["cache_control"] == {"type": "ephemeral"}
    assert "Active LLM categories" in static_block["text"]
    assert "<document>" not in static_block["text"]

    assert doc_block["type"] == "text"
    assert "<document>" in doc_block["text"]
    assert "cache_control" not in doc_block
