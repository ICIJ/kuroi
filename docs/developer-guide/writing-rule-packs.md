# Writing rule packs

A rule pack is a YAML file that lists categories of things to redact
and how each one should be detected. The shipping pack `pii-en.yaml`
covers common English-language PII; you can write your own for other
languages or domains.

## Anatomy of `pii-en.yaml`

```yaml
name: pii-en
display_name: PII (English)
description: Common personally identifiable information in English text.
version: 1
categories:
  - id: email
    label: Email address
    detection: regex
    confidence: high
    pattern: '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

  - id: phone
    label: Phone number
    detection: regex
    confidence: high
    pattern: '\+?\d[\d\s\-]{7,}\d'

  - id: person_name
    label: Person name
    detection: llm
    confidence: medium
    pattern: null
```

The full schema is documented at
[Rule schema reference](../reference/rule-schema.md) (auto-generated
from the dataclasses in `kuroi.core.rules`).

## Regex vs LLM detection

| `detection: regex` | `detection: llm` |
| --- | --- |
| `pattern` is required. | `pattern` is `null`. |
| Match runs locally, no LLM. | The LLM is asked to find spans of this category by `id`. |
| Best for fixed shapes (emails, IBANs, credit-card numbers). | Best for context-dependent things (person names, addresses, internal codenames). |
| `confidence` should usually be `high`. | `confidence` reflects how trustworthy the LLM judge is for this category. |

A category is `regex` *or* `llm`, never both.

## Built-in pack aliases

`kuroi.core.rules._ALIASES` maps short names to packaged packs. The
shipping alias `pii` resolves to `pii-en`. So:

```sh
$ kuroi run document.pdf --rules pii -o document.redacted.pdf
```

is equivalent to `--rules pii-en`. Add aliases when you contribute a
pack with a more specific name.

## Adding a pack to the kuroi distribution

1. Drop your YAML in `src/kuroi/rules/<name>.yaml`.
2. (Optional) Register a short alias in `_ALIASES` if the file name is
   long.
3. Run `kuroi run my.pdf --rules <name> -o out.pdf` to verify the pack
   loads. Schema errors raise on `load_rule_set(name)` with a specific
   message pointing at the offending field.
4. Add a small representative PDF + expected findings in `tests/`.

## Loading a private pack

If you don't want to vendor the pack into kuroi, load it from your own
code. The simplest path is to construct the dataclasses directly:

```python
from pathlib import Path
import yaml

from kuroi.core.rules import Category, RuleSet


def load_my_pack(path: Path) -> RuleSet:
    raw = yaml.safe_load(path.read_text())
    cats = tuple(
        Category(
            id=c["id"],
            label=c["label"],
            detection=c["detection"],
            confidence=c["confidence"],
            pattern=c.get("pattern"),
        )
        for c in raw["categories"]
    )
    return RuleSet(
        name=raw["name"],
        display_name=raw["display_name"],
        description=raw["description"],
        version=int(raw["version"]),
        categories=cats,
    )
```

Then pass the resulting `RuleSet` into `apply_regex_rules` and to your
provider's `detect_redactions`. See
[Using kuroi as a library](library-usage.md) for the full pipeline.

## Tips

- Keep regex patterns tight. A loose pattern produces false positives
  the LLM has to undo, which costs tokens.
- Use the `confidence` field meaningfully — `kuroi diff` does not yet
  filter by it, but the audit JSONL records it for downstream use.
- Bump `version` when the schema changes. The version is recorded in
  the audit log so you can correlate findings to the pack revision
  that produced them.
- Test packs against a small representative PDF in `tests/`.
