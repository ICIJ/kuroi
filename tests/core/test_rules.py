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


def test_category_model_defaults_to_none() -> None:
    from kuroi.core.rules import Category

    cat = Category(
        id="email_llm",
        label="email (llm-only)",
        detection="llm",
        confidence="medium",
        pattern=None,
    )
    assert cat.model is None


def test_load_rule_set_reads_optional_model_field(tmp_path) -> None:
    """A rule pack YAML may declare `model:` per category to route that
    category's calls to a specific model. Categories that omit it inherit
    the run's global model."""
    from kuroi.core.rules import load_rule_set

    pack = tmp_path / "test_pack.yaml"
    pack.write_text(
        """name: test_pack
display_name: "Test"
description: ""
version: 1
categories:
  - id: cheap
    label: "cheap one"
    detection: llm
    confidence: medium
    model: claude-haiku-4-5
  - id: pricey
    label: "pricey one"
    detection: llm
    confidence: medium
"""
    )

    # Patch resources.files to point at tmp_path for this one call.
    import kuroi.core.rules as rules_module

    monkey_files = lambda *_a, **_kw: type(
        "X", (), {"joinpath": lambda self, name: tmp_path / name}
    )()
    original = rules_module.resources.files
    rules_module.resources.files = monkey_files
    try:
        rs = load_rule_set("test_pack")
    finally:
        rules_module.resources.files = original

    by_id = {c.id: c for c in rs.categories}
    assert by_id["cheap"].model == "claude-haiku-4-5"
    assert by_id["pricey"].model is None
