from kuroi.core.pdf import Page, Word
from kuroi.providers._shared import (
    SYSTEM_PROMPT,
    build_system_prompt,
    build_user_prompt,
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
