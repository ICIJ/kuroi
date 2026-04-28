from kuroi.core.findings import Finding
from kuroi.core.pdf import Page, Word
from kuroi.providers.anthropic import build_user_prompt, parse_findings_payload


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
