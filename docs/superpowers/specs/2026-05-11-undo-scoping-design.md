# Scoping `kuroi undo` to File, Page Range, and Element

**Date:** 2026-05-11
**Status:** Draft

## Overview

Extend `kuroi undo` so users can undo:

- **A specific file** — `kuroi undo path/to/file.pdf` (rather than always the
  latest backup overall).
- **A page range** — `kuroi undo file.pdf --pages 3-5` un-redacts every finding
  on pages 3 through 5 from the backup.
- **A specific element** — interactive picker by default, or non-interactive
  filters (`--page`, `--kind`, `--words`) for scripts.

All three modes are implemented by one mechanism: regenerate the output by
re-running `apply_redactions` against the backup with a user-specified subset
of the original findings removed.

## Motivation

Today `kuroi undo` is an all-or-nothing operation: it copies the most recent
backup over its original path. Real workflows are finer-grained:

- A user reviews the redacted output, decides one specific name (or one page)
  was over-redacted, and wants to un-redact just that — without re-running the
  whole pipeline.
- A user runs against several PDFs in a sitting and later wants to roll back
  one of them, not whichever one was last.
- A user wants chain-of-custody for what was put back, not just an unaudited
  file copy.

The audit log already records enough per-finding metadata (page, word range,
bbox, kind, source, text hash) to identify each redaction. The backup PDF
holds the pristine original. Together they let us reconstruct any subset of
the original redaction set deterministically.

## Constraints and prerequisites

- **True redaction is destructive.** `apply_redactions` rewrites the PDF
  content stream — the original glyphs are gone from the output. Per-element
  undo therefore requires the backup. Sessions run with `--no-backup` cannot
  be element-undone.
- **Audit log required for element-level undo.** Full-file restore (no
  selectors) only needs the backup; it works even if the audit log is
  missing.
- **Output-path lock** must be reused so an undo cannot race a concurrent
  `kuroi run` writing the same file.

## Unified mechanism

Every form of undo runs the same five steps:

1. Resolve the session (backup + audit log) from `INPUT` and/or `--session`.
2. Parse all `decision == "applied"` findings from the audit log.
3. Compute an **exclusion set** from CLI filters, optionally union'd with the
   interactive picker's selections.
4. Regenerate the output: `apply_redactions(backup, kept_findings, pages, tmp)`,
   verify the result, then atomically move `tmp` over `output_path`.
5. Write `<undo_ts>.undo.jsonl` recording what was un-redacted and the new
   output hash.

A no-selector, non-TTY invocation short-circuits to "copy backup over
output_path", which preserves today's behavior.

## CLI surface

```
kuroi undo                                          # latest backup overall (back-compat)
kuroi undo INPUT                                    # latest session matching INPUT
kuroi undo INPUT --session <ts>                     # specific session for INPUT
kuroi undo INPUT --pages 3-5                        # un-redact every finding on those pages
kuroi undo INPUT --page 3 --kind email              # AND filter: email findings on page 3
kuroi undo INPUT --page 3 --words 12-14             # AND filter: specific word range on a page
kuroi undo INPUT                                    # picker (TTY + no element selector)
kuroi undo INPUT -y                                 # skip confirmation
kuroi undo INPUT --dry-run                          # print plan, write nothing
kuroi undo INPUT --backup-dir / --audit-dir         # path overrides (existing flag style)
```

Composition rules:

- `--pages`, `--page`, `--kind`, `--words` are AND-composed to define the
  exclusion set.
- `--pages` does not suppress the picker; it pre-filters its rows.
- The picker opens when `stdin.isatty()` AND no `--page`/`--kind`/`--words`
  was given AND `-y` was not passed.
- Picker selections and flag-derived selections compose by union before
  regeneration.

## Locator: from `INPUT` to a session

1. Resolve `backup_dir` and `audit_dir` (CLI overrides → `$XDG_DATA_HOME/kuroi/backups`,
   `…/audit`).
2. If `--session <ts>` is given: open `backup_dir/<ts>/manifest.json`
   directly. Missing → exit 1, suggest `kuroi backups list`.
3. Else if `INPUT` is given: normalize `INPUT` with `Path.resolve(strict=False)`,
   scan all session manifests, apply the same `Path.resolve(strict=False)` to
   each manifest's `original_path`, keep the matches, pick the newest timestamp.
   Missing → exit 1, message names the file and hints at `--no-backup` as a
   likely cause.
4. Else: pick the most recent session overall (today's default).

Once a session is selected, its audit log lives at
`audit_dir/<session_ts>.jsonl`. If missing **and** any element selector was
given (`--page`, `--kind`, `--words`, or picker), exit 3 with
`audit log not found for session <ts>; element-level undo requires the audit log`.
If missing and only a full restore was requested, skip the audit and copy the
backup over `output_path`.

## Parsing findings and regenerating

A new module `kuroi.core.audit_replay` exposes:

```python
@dataclass(frozen=True)
class ReplayableFinding:
    page: int
    word_start: int
    word_end: int
    kind: str
    confidence: Confidence
    source: str
    bbox: tuple[float, float, float, float] | None  # sanity check only

@dataclass(frozen=True)
class ReplayableSession:
    session_id: str
    input_path: Path
    output_path: Path
    findings: tuple[ReplayableFinding, ...]   # decision == "applied", audit-log order

def load_session(audit_path: Path) -> ReplayableSession: ...
def build_exclusion_set(
    findings: tuple[ReplayableFinding, ...],
    *,
    pages: tuple[int, ...] | None,
    page: int | None,
    kind: str | None,
    words: tuple[int, int] | None,
    picker_indices: tuple[int, ...],
) -> frozenset[int]: ...
```

Regeneration:

```
backup_pdf    = manifest.copy_path
pages         = pdf.extract_word_index(backup_pdf)     # re-extract from backup
kept          = [to_finding(f) for i, f in enumerate(session.findings)
                 if i not in excluded_indices]
temp_out      = output_path.with_suffix(output_path.suffix + ".kuroi-undo-tmp")
apply_redactions(backup_pdf, kept, pages, temp_out)
report = verify_pdf(temp_out)
if not report.passed:
    raise UndoVerificationFailed(...)                  # exit 4
shutil.move(temp_out, output_path)
```

Word indices come from the backup itself; bboxes recompute identically to the
original run, so the regenerate is deterministic.

Short-circuits:

- **No selector path** (no `--pages`, no element flags, no picker — e.g.,
  non-TTY `kuroi undo INPUT`): skip the exclusion-set machinery entirely.
  Copy the backup over `output_path` and emit a normal `<undo_ts>.undo.jsonl`
  with `findings_excluded` equal to the full count. This preserves today's
  behavior.
- **Exclusion set was built but empty** (filters matched nothing, or the
  user closed the picker with zero selections): exit 6 with `nothing to undo`.
- **Exclusion set contains every finding**: skip the PyMuPDF call, copy the
  backup over `output_path`. Still emit a normal `<undo_ts>.undo.jsonl`.

## Interactive picker

**Library.** New runtime dependency: `questionary>=2.0`. Stock `questionary.checkbox`
gives arrow navigation, space-to-toggle, enter-to-confirm.

**Rows.** For each `ReplayableFinding`, extract the redacted text from the
backup PDF (`page.words[word_start..word_end]` joined with spaces) and build
a `Choice`. Page-header rows (sentinel value `("page", N)`) precede each
page's findings and act as group toggles — our wrapper expands a selected
page-header into all of that page's finding indices, then dedupes.

**Layout.**

```
Session 2026-05-11T14-22-13Z-abc123 — myfile.pdf
Use ↑/↓ to move, Space to toggle, Enter to confirm, Ctrl-C to cancel.

 ▸ [ ] Page 3   (3 findings)
     [ ] email      llm     "john.doe@example.com"
     [ ] person     rules   "John Doe"
     [ ] phone      llm     "+33 1 23 45 67 89"
   [ ] Page 5   (2 findings)
     [ ] person     rules   "Marie Curie"
     [ ] date       llm     "April 14, 1934"
```

**Truncation.** Each row's text is truncated to fit
`shutil.get_terminal_size().columns - prefix`, ending in a Unicode ellipsis.

**Scale.** Stock questionary scrolls fine up to a few hundred rows. For huge
docs, the user narrows the picker with `--pages X-Y`. No collapsible groups
in v1.

**Confirmation.** After Enter, we print a summary and prompt with
`typer.confirm`:

```
Will un-redact 2 findings (regenerate myfile.pdf from backup):
  p.3  email   "john.doe@example.com"
  p.7  email   "marie@example.fr"

Proceed? [Y/n]
```

`-y` skips the prompt. `--dry-run` runs the picker (to let the user see the
plan) but exits 0 after printing the summary without writing anything.

**Picker indirection.** The picker is wrapped in a thin internal function
`pick_findings(rows) -> tuple[int, ...]` so tests can monkeypatch the picker
directly without driving a fake TTY.

## Undo audit log

Written to `audit_dir/<undo_ts>.undo.jsonl`, mode `0600`, parent `0700` —
same hardening as `AuditLog.open`. Schema:

**`undo_start`**

```json
{
  "event": "undo_start",
  "audit_schema_version": 1,
  "schema_kind": "undo",
  "undo_id": "<uuid4>",
  "ts_start": "2026-05-11T15:08:44Z",
  "kuroi_version": "x.y.z",
  "source_session_id": "<uuid4 from run>",
  "source_session_ts": "2026-05-11T14-22-13Z-abc123",
  "source_audit_path": "/.../audit/<source_ts>.jsonl",
  "input_path": "/.../myfile.pdf",
  "output_path": "/.../myfile.pdf",
  "backup_path": "/.../backups/<source_ts>/myfile.pdf",
  "selector": {
    "interactive": true,
    "pages": null,
    "page": null,
    "kind": null,
    "words": null
  },
  "findings_total": 8,
  "findings_excluded": 2
}
```

**`undo_finding`** (one per un-redacted finding)

```json
{
  "event": "undo_finding",
  "ts": "2026-05-11T15:08:44Z",
  "page": 3,
  "word_start": 12,
  "word_end": 14,
  "kind": "email",
  "source": "llm",
  "text_sha256": "<copied from source audit>",
  "context_sha256": "<copied from source audit>"
}
```

Plaintext is never written to the undo log, even if `audit_include_text` is
on for runs. The hash references the source log; a forensic reviewer
correlates the two files.

**`undo_end`**

```json
{
  "event": "undo_end",
  "ts_end": "2026-05-11T15:08:45Z",
  "status": "ok",
  "duration_ms": 412,
  "redactions_kept": 6,
  "redactions_un_redacted": 2,
  "verify_result": "pass",
  "verify_leak_count": 0,
  "output_sha256": "<hex>"
}
```

`status` is `"ok" | "verify_failed" | "failed"`. On any failure path,
`undo_end` is still emitted; the existing output file is not touched.

**Immutability.** The source run's NDJSON is never reopened or rewritten.
The undo log references it by path and session id. Chronological history
across runs and undos is `ls audit_dir/*.jsonl`.

New module: `kuroi.core.undo_audit` mirroring the shape of `kuroi.core.audit`
with `UndoAuditLog.open(...)`, `write_undo_finding(...)`, and `close(...)`
as a context manager.

## Error handling and exit codes

Additive to today's `kuroi undo` codes:

| Code | Condition |
|------|-----------|
| 0 | Undo succeeded, user declined confirmation, or `--dry-run` printed the plan |
| 1 | No backup matched (selector/path lookup failed, or backup hash mismatch) |
| 2 | Config or CLI argument error |
| 3 | Audit log missing for the resolved session but element-level undo was requested |
| 4 | Verification failed on the regenerated PDF; existing output untouched |
| 5 | Output-path lock held by another `kuroi run` or `kuroi undo` |
| 6 | Empty exclusion set after applying filters (`nothing to undo`) |
| 130 | User hit Ctrl-C in the picker |

Single-line, actionable error messages, same style as existing kuroi CLI.

**Backup integrity.** Before regenerating, hash the backup file and compare
against `input_sha256` from the source `session_start`. Mismatch → exit 1
with `backup file modified since run — refusing to regenerate`.

**Locking.** The regenerate-and-move step runs under
`output_lock(output_path)` from `kuroi.core.locks`. Held lock → exit 5 via
the existing `LockHeldError`.

**Verification gate.** The regenerated PDF goes through `verify_pdf` from
`kuroi.core.verification`. On leak detection: delete `temp_out`, leave
`output_path` untouched, emit `undo_end` with `status="verify_failed"`,
exit 4. This is a strict subset of an already-verified run, so it should
never fire; the gate is cheap insurance.

## Behavior matrix

| Invocation                                    | Picker         | Confirmation         | Exclusion set source       |
|-----------------------------------------------|----------------|----------------------|----------------------------|
| `kuroi undo INPUT` (TTY)                      | yes            | yes                  | picker                     |
| `kuroi undo INPUT` (no TTY)                   | no             | yes                  | empty → full restore       |
| `kuroi undo INPUT --pages 3-5` (TTY)          | yes (filtered) | yes                  | picker                     |
| `kuroi undo INPUT --pages 3-5 -y`             | no             | no                   | all findings on pages 3–5  |
| `kuroi undo INPUT --page 3 --kind email`      | no             | yes                  | filter                     |
| `kuroi undo INPUT --page 3 --kind email -y`   | no             | no                   | filter                     |
| `kuroi undo INPUT --dry-run`                  | yes (TTY)      | summary, no write    | depends on flags           |

## Testing

Pattern: `pytest` + `typer.testing.CliRunner` + the existing `make_pdf`
fixture in `tests/conftest.py`.

New test files:

- `tests/core/test_audit_replay.py` — pure-function tests for `load_session`
  and `build_exclusion_set`.
- `tests/core/test_undo_audit.py` — `UndoAuditLog.open` permissions, schema,
  atomic close on exceptions. Mirrors `tests/core/test_audit.py`.
- `tests/cli/test_undo.py` — extend with the new flag/picker matrix.

Coverage:

*Audit-log parsing*

- Parses a real session log produced by running `kuroi run` against a fixture.
- Skips `decision != "applied"`.
- Tolerates unknown fields.
- Errors cleanly on truncated/corrupt NDJSON.

*Exclusion-set builder*

- `--pages 3-5` → indices for findings on those pages.
- `--page 3 --kind email` → AND composition.
- `--page 3 --words 12-14` → exact word-range match.
- Empty result → empty set (CLI maps to exit 6).
- Picker indices + flag filters compose by union.

*Locator*

- Two sessions for the same file → newest wins.
- `--session` picks the exact one.
- No match → exit 1, message names the file.
- Missing audit log + element selector → exit 3.

*End-to-end regeneration*

- Run `kuroi run` on a fixture producing ≥3 findings; then
  `kuroi undo --page X --kind Y -y`. Assert output changed, hash differs,
  `verify_pdf` passes, `<ts>.undo.jsonl` is well-formed, source log is
  byte-identical.
- Full restore path (no selectors, non-TTY) → output bytes equal backup bytes.
- Excluding every finding → short-circuit; output bytes equal backup bytes;
  PyMuPDF regenerate path is not invoked.
- Backup hash mismatch → exit 1.
- Output lock held → exit 5.
- `--no-backup` followed by undo → exit 1, error mentions `--no-backup`.

*Verification gate*

- Monkeypatch `verify_pdf` to return `passed=False` once → temp file deleted,
  original output untouched, `undo_end.status == "verify_failed"`, exit 4.

*Picker*

- Monkeypatch `pick_findings` directly. Three tests: empty selection
  → exit 6; single selection → that finding excluded; page-header selection
  → all rows on that page excluded.
- One smoke test: `sys.stdin.isatty()` patched to `False` → picker never
  called, full-restore path runs.

*Dry-run*

- `--dry-run --page 3 -y` → exit 0, no temp file created, no undo audit log
  written, output mtime unchanged.

Explicit non-goals:

- Live questionary key handling (trust the library; test our wrapper).
- PDF-render fidelity of regenerated output (already covered by `kuroi run`
  tests; same `apply_redactions` code path).
- Performance benchmarks (add only when a real regression appears).
