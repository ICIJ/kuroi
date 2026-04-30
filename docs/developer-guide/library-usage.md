# Using kuroi as a library

kuroi can be imported into a Python pipeline. This page covers the
stable public API; everything else (anything not listed here) is
internal and may change without notice.

## The public surface

| Symbol                                     | Purpose                                       |
| ------------------------------------------ | --------------------------------------------- |
| `kuroi.core.redaction.apply_redactions`    | Write a redacted copy of a PDF.               |
| `kuroi.core.findings.Finding`              | The detector output type.                     |
| `kuroi.core.findings.bbox_union`           | Merge bounding boxes over a span.             |
| `kuroi.core.rules.RuleSet` / `Category`    | Rule pack data types.                         |
| `kuroi.core.rules.load_rule_set`           | Load a built-in rule pack by name.            |
| `kuroi.core.rules.apply_regex_rules`       | Run the regex categories over word-indexed pages. |
| `kuroi.core.rules.llm_categories`          | Filter a RuleSet to its LLM categories.       |
| `kuroi.core.pdf.extract_word_index`        | Extract word-indexed pages from a PDF.        |
| `kuroi.providers.base.Provider`            | The Protocol every LLM client implements.     |
| `kuroi.providers.factory.make_provider`    | Build a provider from a `Config`.             |
| `kuroi.core.config.Config`                 | The resolved configuration.                   |
| `kuroi.core.config.ConfigOverrides`        | CLI-flag overlay struct.                      |
| `kuroi.core.config.resolve_config`         | Resolve config from CLI/env/file/defaults.    |
| `kuroi.core.config.xdg_config_home`        | Locate the user config directory.             |

For the full type signatures, see [the Python API reference](../reference/api/redaction.md).

## Minimal example

```python
import os
from pathlib import Path

from kuroi.core.config import (
    ConfigOverrides,
    resolve_config,
    xdg_config_home,
)
from kuroi.core.pdf import extract_word_index
from kuroi.core.redaction import apply_redactions
from kuroi.core.rules import (
    apply_regex_rules,
    llm_categories,
    load_rule_set,
)
from kuroi.providers.factory import make_provider

cfg = resolve_config(
    ConfigOverrides(),
    env=os.environ,
    file_path=xdg_config_home() / "kuroi" / "config.toml",
)
ruleset = load_rule_set("pii-en")
pages = extract_word_index(Path("report.pdf"))

# Regex pass
findings = apply_regex_rules(pages, ruleset)

# LLM pass
provider = make_provider(cfg)
llm_findings, _chunks = provider.detect_redactions(
    pages,
    tuple(c.id for c in llm_categories(ruleset)),
)
findings.extend(llm_findings)

# Apply
apply_redactions(
    pdf_path=Path("report.pdf"),
    findings=findings,
    pages=pages,
    output_path=Path("report.redacted.pdf"),
)
```

`resolve_config` is keyword-strict for `env` and `file_path` — pass
`os.environ` for the env mapping and the path to your `config.toml`.

## Error types

- `kuroi.core.config.ConfigError` — raised by `resolve_config` when
  the merged configuration is invalid (unknown provider, wrong type
  in the file, missing required Ollama model).
- `pymupdf` raises `RuntimeError` for unparseable PDFs. Catch broadly
  around `extract_word_index`.

## What's *not* part of the public API

- Anything under `kuroi.cli.*` — the CLI is for end users.
- Internal config helpers (`_read_string`, `load_config_file`,
  `write_config_file`) — names lead with `_` or are file-format-specific.
- The on-disk shapes under `~/Documents/kuroi-backups/` and
  `~/.local/share/kuroi/audit/` — internal, versioned.
- `providers/_shared.py` — implementation helpers, name leads with `_`.
- The exact prompt text sent to the LLM — refined release-to-release.

If you need something that isn't on the public list, open an issue. We
can promote internal helpers if there's a real consumer.
