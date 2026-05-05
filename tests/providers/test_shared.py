from kuroi.core.pdf import Page, Word
from kuroi.providers._shared import SYSTEM_PROMPT, build_user_prompt


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


def test_system_prompt_covers_instructions() -> None:
    assert "redaction instructions" in SYSTEM_PROMPT.lower()
