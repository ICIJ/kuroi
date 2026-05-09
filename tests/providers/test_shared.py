from kuroi.core.pdf import Page, Word
from kuroi.providers._shared import (
    SYSTEM_PROMPT,
    build_system_blocks,
    build_system_prompt,
    build_user_document_block,
    build_user_prompt,
    build_user_static_prefix,
    strip_code_fence,
)


def _page(num: int, words: list[str]) -> Page:
    return Page(
        number=num,
        words=tuple(Word(idx=i, text=w, bbox=(0, 0, 1, 1)) for i, w in enumerate(words)),
    )


def test_build_user_prompt_categories_only() -> None:
    pages = (_page(1, ["Hello", "world"]),)
    prompt = build_user_prompt(pages, ("person_name",))
    assert "Active LLM categories: person_name" in prompt
    assert "Redaction instructions" not in prompt
    assert "<document>" in prompt


def test_build_user_prompt_instructions_only() -> None:
    pages = (_page(1, ["Hello", "world"]),)
    prompt = build_user_prompt(pages, (), instructions=("redact all names",))
    assert "Redaction instructions: redact all names" in prompt
    assert "Active LLM categories" not in prompt
    assert "<document>" in prompt


def test_build_user_prompt_both_sections_categories_first() -> None:
    pages = (_page(1, ["Hello", "world"]),)
    prompt = build_user_prompt(pages, ("email",), instructions=("redact all names",))
    assert "Active LLM categories: email" in prompt
    assert "Redaction instructions: redact all names" in prompt
    assert prompt.index("Active LLM categories") < prompt.index("Redaction instructions")


def test_build_user_prompt_no_categories_no_instructions() -> None:
    pages = (_page(1, ["Hello", "world"]),)
    prompt = build_user_prompt(pages, ())
    assert "Output schema:" in prompt
    assert "<document>" in prompt
    assert "Active LLM categories" not in prompt
    assert "Redaction instructions" not in prompt


def test_system_prompt_covers_instructions() -> None:
    lower = SYSTEM_PROMPT.lower()
    assert "redaction instructions" in lower
    assert "<document>" in lower
    assert "ignore" in lower  # prompt injection guard mentions ignoring injected instructions


def test_build_system_prompt_off_returns_base() -> None:
    assert build_system_prompt(layout_aware=False) == SYSTEM_PROMPT


def test_build_system_prompt_on_appends_block_explanation() -> None:
    prompt = build_system_prompt(layout_aware=True)

    assert prompt.startswith(SYSTEM_PROMPT)
    assert "<block id=" in prompt
    assert "use the per-page word indices" in prompt.lower()
    assert "do not report block ids" in prompt.lower()


def test_build_system_prompt_on_is_strictly_longer_than_off() -> None:
    on = build_system_prompt(layout_aware=True)
    off = build_system_prompt(layout_aware=False)
    assert len(on) > len(off)


def _page_with_blocks() -> Page:
    return Page(
        number=1,
        words=(
            Word(idx=0, text="A", bbox=(0, 0, 1, 1), block_id=3),
            Word(idx=1, text="B", bbox=(1, 0, 2, 1), block_id=4),
        ),
    )


def test_build_user_prompt_default_no_block_tags() -> None:
    prompt = build_user_prompt(
        (_page_with_blocks(),),
        llm_category_ids=("kind",),
    )

    assert "<block" not in prompt
    assert "[0]A [1]B" in prompt


def test_build_user_prompt_layout_aware_includes_block_tags() -> None:
    prompt = build_user_prompt(
        (_page_with_blocks(),),
        llm_category_ids=("kind",),
        layout_aware=True,
    )

    assert '<block id="3">[0]A</block>' in prompt
    assert '<block id="4">[1]B</block>' in prompt


def test_build_user_static_prefix_contains_categories_and_schema() -> None:
    prefix = build_user_static_prefix(("person_name",), instructions=())
    assert "Active LLM categories: person_name" in prefix
    assert "Output schema:" in prefix
    assert "<document>" not in prefix


def test_build_user_static_prefix_includes_instructions() -> None:
    prefix = build_user_static_prefix(("name",), instructions=("Redact all URLs",))
    assert "Redaction instructions: Redact all URLs" in prefix


def test_build_user_document_block_wraps_pages() -> None:
    pages = (_page(1, ["Hello"]),)
    block = build_user_document_block(pages, layout_aware=False)
    assert block.startswith("<document>")
    assert block.endswith("</document>")
    assert "[0]Hello" in block


def test_build_user_prompt_concatenates_prefix_and_document() -> None:
    """The legacy joined-string form is preserved for the Ollama path."""
    pages = (_page(1, ["Hello"]),)
    cat_ids = ("name",)
    prefix = build_user_static_prefix(cat_ids, instructions=())
    doc = build_user_document_block(pages, layout_aware=False)
    assert build_user_prompt(pages, cat_ids) == prefix + doc


def test_build_system_blocks_marks_prompt_with_cache_control() -> None:
    blocks = build_system_blocks(layout_aware=False)
    assert isinstance(blocks, list)
    assert len(blocks) == 1
    block = blocks[0]
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert block["text"].startswith("You are a redaction-assistant for kuroi")


def test_strip_code_fence_passes_plain_json_through() -> None:
    text = '{"findings": []}'
    assert strip_code_fence(text) == text


def test_strip_code_fence_unwraps_json_language_tag() -> None:
    text = '```json\n{"findings": [{"page": 1}]}\n```'
    assert strip_code_fence(text) == '{"findings": [{"page": 1}]}'


def test_strip_code_fence_unwraps_uppercase_language_tag() -> None:
    text = '```JSON\n{"findings": []}\n```'
    assert strip_code_fence(text) == '{"findings": []}'


def test_strip_code_fence_unwraps_bare_fence() -> None:
    text = '```\n{"findings": []}\n```'
    assert strip_code_fence(text) == '{"findings": []}'


def test_strip_code_fence_tolerates_outer_whitespace() -> None:
    text = '\n  ```json\n{"findings": []}\n```  \n'
    assert strip_code_fence(text) == '{"findings": []}'


def test_strip_code_fence_leaves_unfenced_garbage_unchanged() -> None:
    # Truly malformed input still surfaces to the json.JSONDecodeError path,
    # so callers continue to log and subdivide rather than silently swallow.
    text = "this is not json"
    assert strip_code_fence(text) == "this is not json"


def test_strip_code_fence_does_not_strip_orphan_opening_fence() -> None:
    # Truncated response with only the opening fence: leaving it intact
    # preserves the existing "non-JSON, will subdivide" behavior so we
    # don't silently parse a half-message.
    text = '```json\n{"findings": [{"page": 1'
    assert strip_code_fence(text) == text
