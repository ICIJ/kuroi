"""Tests for the instruction decomposer module."""

from kuroi.core.instruction_decomposer import parse_instruction


def test_parser_splits_numbered_list() -> None:
    rules = parse_instruction("1. Redact emails\n2. Redact phone numbers")
    assert rules == ("1. Redact emails", "2. Redact phone numbers")


def test_parser_splits_bulleted_list_with_dash() -> None:
    rules = parse_instruction("- Redact emails\n- Redact phone numbers")
    assert rules == ("- Redact emails", "- Redact phone numbers")


def test_parser_splits_bulleted_list_with_asterisk() -> None:
    rules = parse_instruction("* Redact emails\n* Redact phone numbers")
    assert rules == ("* Redact emails", "* Redact phone numbers")


def test_parser_splits_blank_line_paragraphs() -> None:
    rules = parse_instruction(
        "Redact all email addresses in the document.\n\nAlso redact phone numbers."
    )
    assert rules == (
        "Redact all email addresses in the document.",
        "Also redact phone numbers.",
    )


def test_parser_returns_single_rule_for_prose() -> None:
    rules = parse_instruction("Redact all PII")
    assert rules == ("Redact all PII",)


def test_parser_ignores_inline_numbers() -> None:
    """Inline numbers like phone digits or ZIPs must not trigger split.
    Anchor `^\\d+\\.` to line start prevents this."""
    rules = parse_instruction(
        "Redact ZIPs like 12345 and phone numbers like 555-1234 too."
    )
    assert rules == ("Redact ZIPs like 12345 and phone numbers like 555-1234 too.",)


def test_parser_strips_whitespace_around_rules() -> None:
    rules = parse_instruction("  1. foo  \n  2. bar  ")
    assert rules == ("1. foo", "2. bar")


def test_parser_drops_empty_rules() -> None:
    """A `2.` line with no content should be filtered out, not produce an empty rule."""
    rules = parse_instruction("1. foo\n2. \n3. bar")
    assert rules == ("1. foo", "3. bar")


def test_parser_numbering_takes_precedence_over_bullets() -> None:
    """If both numbered and bulleted markers appear, numbered split wins."""
    rules = parse_instruction("1. First numbered\n- bullet inside\n2. Second numbered")
    assert len(rules) == 2
    assert rules[0].startswith("1.")
    assert rules[1].startswith("2.")


def test_parser_handles_empty_input() -> None:
    """Empty or whitespace-only input returns a one-element tuple of empty string.
    The CLI gate prevents this case in practice; defensive fallback only."""
    assert parse_instruction("") == ("",)
    assert parse_instruction("   \n  ") == ("",)


def test_parser_handles_real_world_pacer_example() -> None:
    """Smoke test against the user's actual instruction shape."""
    instruction = (
        "1. All URLs (because some lead to bad sites). "
        "Redact the entire 'links' column.\n"
        "2. The names of the complainants in the 'content' column.\n"
        "3. The email addresses in the 'email' column.\n"
        "4. The IP addresses of the complainant.\n"
        "5. The 'lastreplier' column."
    )
    rules = parse_instruction(instruction)
    assert len(rules) == 5
    assert rules[0].startswith("1.")
    assert rules[4].startswith("5.")
