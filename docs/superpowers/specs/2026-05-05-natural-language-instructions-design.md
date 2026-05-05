# Natural-Language Redaction Instructions

**Date:** 2026-05-05
**Status:** Approved

## Overview

Add support for free-text redaction instructions such as:

> "Redact the URLs to the videos, the names of the complainants, the complainants' email addresses, and their IP addresses."

Instructions are passed via `--instruct` / `-i` on the CLI, or entered interactively when neither `--rules` nor `--instruct` is given. When active, instructions are forwarded to the LLM provider, which returns findings in the same JSON format as category-based detection.

## Behaviour matrix

| `--rules` | `--instruct` | `-y` | Result |
|-----------|--------------|------|--------|
| (default empty) | (not given) | no | Interactive prompt for instructions |
| (default empty) | (not given) | yes | Error: pass `--rules`, `--instruct`, or both |
| non-empty | (not given) | any | Current behaviour — rules only |
| (empty) | non-empty | any | Instruction-only — no regex, no LLM categories |
| non-empty | non-empty | any | Both run; regex + LLM categories + instruction in one LLM call |

## Architecture

### CLI (`cli/run.py`)

- Change `rules` default from `"pii"` to `""`.
- Add `--instruct` / `-i` (`str | None`, default `None`).
- Before loading rule sets, evaluate `has_rules` and `has_instruct`:
  - Neither + non-interactive (`-y`): print error, exit code 2.
  - Neither + interactive: `instruct = typer.prompt("Redaction instructions")`.
- Pass `instructions=({"text": instruct},)` (or `()`) to `AuditLog.open()` — the field already exists in the audit schema (type `tuple[dict, ...]`, distinct from the `tuple[str, ...]` passed to `detect_redactions`).

### Provider protocol (`providers/base.py`)

Add `instructions: tuple[str, ...] = ()` to `detect_redactions()`. Keyword-only, defaults to empty so all existing call sites compile without changes.

### Shared helpers (`providers/_shared.py`)

- Move `build_user_prompt` here (currently copy-pasted in both providers).
- Move `SYSTEM_PROMPT` and `OUTPUT_SCHEMA_HINT` constants here for the same reason.
- Update `build_user_prompt(pages, llm_category_ids, instructions=())`:
  - Categories present → prepend `"Active LLM categories: <ids>\n\n"`.
  - Instructions present → append `"Redaction instructions: <text>\n\n"`.
  - Both: categories section first, then instructions section.
- Update system prompt rule 2: "Identify candidate redactions according to the LLM categories and/or redaction instructions provided by the user."

### Provider implementations (`anthropic.py`, `ollama.py`)

1. Add `instructions: tuple[str, ...] = ()` to `detect_redactions()`.
2. Change early-return guard: `if not llm_category_ids and not instructions: return [], []`.
3. Import and call `build_user_prompt` from `_shared` (delete local copy).
4. Derive `source` for `parse_findings_payload`:
   - `llm_category_ids` empty → `source = "instruction"`
   - otherwise → `source = "llm"` (mixed call; cannot distinguish per-finding origin)

## Data flow

```
CLI parse args
  └─ has_rules=False, has_instruct=False, interactive → prompt user
  └─ has_instruct=True, has_rules=False → skip rule_sets loading
  └─ both → load rule_sets normally

run.py
  ├─ apply_regex_rules(pages, rs)          [only if has_rules]
  ├─ llm_cat_ids from llm_categories(rs)   [only if has_rules]
  └─ provider.detect_redactions(pages, tuple(llm_cat_ids), instructions=(instruct,))
       └─ build_user_prompt includes categories and/or instructions
       └─ single LLM call
       └─ parse_findings_payload → source="instruction" or "llm"

merge findings → apply_redactions → verify → audit
```

## Audit log

`AuditLog.open()` already accepts `instructions: tuple[dict[str, Any], ...]`. Pass `({"text": instruct},)` when an instruction is active, `()` otherwise. No schema version bump needed — the field exists and was already serialised as `[]` in all prior runs.

## Out of scope

- Multiple `--instruct` flags (single string only for now).
- `--instruct-file` / file-based instructions.
- Per-finding source disambiguation in mixed (rules + instructions) runs.
