from collections.abc import Callable
from pathlib import Path

from kuroi.core.pdf import extract_word_index
from kuroi.core.rules import apply_regex_rules, llm_categories, load_rule_set


def test_load_pii_en_has_email_category() -> None:
    rs = load_rule_set("pii-en")
    ids = {c.id for c in rs.categories}
    assert {"email", "ssn_us", "phone_us", "iban", "person_name"}.issubset(ids)


def test_pii_alias_resolves_to_pii_en() -> None:
    rs = load_rule_set("pii")
    assert rs.name == "pii-en"


def test_apply_regex_rules_finds_email(make_pdf: Callable[..., Path]) -> None:
    pdf = make_pdf(["Contact alice@example.com for details"])
    pages = extract_word_index(pdf).pages
    rs = load_rule_set("pii-en")

    findings = apply_regex_rules(pages, rs)

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "email"
    assert f.confidence == "high"
    assert f.source == "rules:pii-en"
    word = pages[0].words[f.start]
    assert "alice@example.com" in word.text


def test_llm_categories_lists_only_llm_kinds() -> None:
    rs = load_rule_set("pii-en")
    cats = llm_categories(rs)
    ids = {c.id for c in cats}
    assert ids == {"person_name", "street_address"}
