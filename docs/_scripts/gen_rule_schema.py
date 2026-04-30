"""Generate `docs/reference/rule-schema.md` from the kuroi.core.rules dataclasses.

Driven by mkdocs-gen-files at build time. Importing this module from a test
does not trigger the gen-files write — `main()` only runs when invoked from
inside a real mkdocs build, detected by the presence of `mkdocs_gen_files`
in `sys.modules` at import time.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from kuroi.core.rules import Category, RuleSet  # noqa: E402


_INTRO = """\
# Rule schema

Auto-generated from the dataclasses in `kuroi.core.rules`. This page is
the source of truth for what a rule pack YAML file may contain. The
[Writing rule packs](../developer-guide/writing-rule-packs.md) guide
walks through using these fields end-to-end.
"""


def _format_type(t: object) -> str:
    return str(t).replace("typing.", "").replace("kuroi.core.", "")


def _field_table(cls: type) -> str:
    rows = ["| Field | Type | Description |", "| --- | --- | --- |"]
    for f in dataclasses.fields(cls):
        type_str = _format_type(f.type)
        desc = f.metadata.get("description", "") if f.metadata else ""
        rows.append(f"| `{f.name}` | `{type_str}` | {desc} |")
    return "\n".join(rows)


def render_schema_markdown() -> str:
    parts = [_INTRO, "", "## RuleSet", "", _field_table(RuleSet), ""]
    parts += ["## Category", "", _field_table(Category), ""]
    parts.append(
        "Refer to [`kuroi.core.rules`](api/rules.md) for the live "
        "definitions and helper functions (`load_rule_set`, "
        "`apply_regex_rules`)."
    )
    return "\n".join(parts) + "\n"


def main() -> None:
    """Entry point for mkdocs-gen-files."""
    import mkdocs_gen_files

    with mkdocs_gen_files.open("reference/rule-schema.md", "w") as fd:
        fd.write(render_schema_markdown())
    mkdocs_gen_files.set_edit_path(
        "reference/rule-schema.md", "src/kuroi/core/rules.py"
    )


# mkdocs-gen-files imports its own module before invoking each script; tests
# that load this file directly do not, so sys.modules is the signal.
if "mkdocs_gen_files" in sys.modules:
    main()
