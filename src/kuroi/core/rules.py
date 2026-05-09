"""Rule sets: declarative detector definitions, partly regex, partly LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from typing import Literal

import yaml

from kuroi.core.findings import Confidence, Finding
from kuroi.core.pdf import Page

DetectionKind = Literal["regex", "llm"]

_ALIASES = {"pii": "pii-en"}


@dataclass(frozen=True)
class Category:
    """A single detector definition within a rule set.

    `detection` selects between regex matching (with `pattern`) and LLM
    delegation (pattern unused). `confidence` is the level every Finding
    emitted by this category will carry.
    """

    id: str
    label: str
    detection: DetectionKind
    confidence: Confidence
    pattern: str | None  # only for detection == "regex"
    model: str | None = None  # if set, routes this category's calls to the named model


@dataclass(frozen=True)
class RuleSet:
    """A named bundle of detector categories loaded from a rule pack YAML."""

    name: str
    display_name: str
    description: str
    version: int
    categories: tuple[Category, ...]


def load_rule_set(name: str) -> RuleSet:
    """Load a built-in rule set by name. `pii` aliases to `pii-en`."""
    resolved = _ALIASES.get(name, name)
    yaml_text = resources.files("kuroi.rules").joinpath(f"{resolved}.yaml").read_text()
    raw = yaml.safe_load(yaml_text)
    cats: list[Category] = []
    for c in raw["categories"]:
        cats.append(
            Category(
                id=c["id"],
                label=c["label"],
                detection=c["detection"],
                confidence=c["confidence"],
                pattern=c.get("pattern"),
                model=c.get("model"),
            )
        )
    return RuleSet(
        name=raw["name"],
        display_name=raw["display_name"],
        description=raw["description"],
        version=int(raw["version"]),
        categories=tuple(cats),
    )


def llm_categories(rs: RuleSet) -> tuple[Category, ...]:
    """Subset of categories the LLM is responsible for."""
    return tuple(c for c in rs.categories if c.detection == "llm")


def apply_regex_rules(pages: tuple[Page, ...], rs: RuleSet) -> list[Finding]:
    """Apply every regex category in `rs` against the word-indexed pages.

    A regex match that spans multiple words emits one Finding spanning the
    matched word range. Matches that span 0 words (e.g. a regex matching only
    inside a single word substring) collapse to that word's index.
    """
    findings: list[Finding] = []
    for cat in rs.categories:
        if cat.detection != "regex" or not cat.pattern:
            continue
        regex = re.compile(cat.pattern)
        for page in pages:
            text, char_to_word = _build_page_text(page)
            for m in regex.finditer(text):
                start_word = char_to_word[m.start()]
                end_word = char_to_word[max(m.end() - 1, m.start())]
                findings.append(
                    Finding(
                        page=page.number,
                        start=start_word,
                        end=end_word,
                        kind=cat.id,
                        confidence=cat.confidence,
                        source=f"rules:{rs.name}",
                    )
                )
    return findings


def _build_page_text(page: Page) -> tuple[str, list[int]]:
    """Concatenate the page's words with single spaces, plus a char→word map.

    Returns (text, char_to_word) where char_to_word[i] is the word index that
    contains character offset i. Spaces between words map to the previous word.
    """
    parts: list[str] = []
    char_to_word: list[int] = []
    cursor = 0
    for w in page.words:
        if cursor > 0:
            parts.append(" ")
            char_to_word.append(w.idx - 1)
            cursor += 1
        parts.append(w.text)
        char_to_word.extend([w.idx] * len(w.text))
        cursor += len(w.text)
    return "".join(parts), char_to_word
