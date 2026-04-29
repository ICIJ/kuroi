# Design gaps resolution

Status: design — ready for plan.
Companion to: `kuroi-design.md`.
Date: 2026-04-29.

## Why this spec exists

`kuroi-design.md` covers eight use cases and a stack-and-architecture section in 1300+ lines, but a careful reading surfaced 17 places where the design is silent, ambiguous, or contradicts itself. These gaps are not large enough to warrant their own specs; left unresolved, they would each be re-decided every time an implementer touches the area, with no guarantee of consistency.

This spec resolves all 17. Each resolution is small. The aggregate purpose is to make the existing design buildable without further design work for these specific points. New features still get their own specs.

The 17 gaps are grouped into five thematic clusters. Within each gap, the resolution either commits to a behavior (most cases) or commits to constraints any future implementation must honor (gap 17, `--corpus-mode`, deferred to v1.1+).

## Changes to `kuroi-design.md`

A follow-up edit to `kuroi-design.md` should:

- Remove all references to telemetry: section 4 of `kuroi init` (lines 242-248), the `[privacy] telemetry = false` line in `kuroi config` (line 851), the description in section 8 ("It does not phone home...", line 1107), and the open-question framing about telemetry endpoints.
- Replace the example `kuroi run leak.pdf --in-place --backup` (line 1027) with `kuroi run leak.pdf --in-place` — `--backup` is not a real flag because backup is mandatory.
- Add a one-line cross-reference to this spec in the "Open design questions" section noting that the listed open questions for items 4-6, 8 are deferred but the small ambiguities in items 1, 2, 3, 7 are now resolved.

## Cluster 1 — Cost and reproducibility

### 1.1 Cost estimation formula

Pricing data lives in `data/pricing.json`, keyed by `(provider, model)`, with `input_per_million` and `output_per_million` (USD). The file is updated only by `kuroi config refresh-pricing`; kuroi never auto-fetches new pricing in the background.

Pre-flight estimate for cloud providers:

```
input_tokens   = tokenize(document_text) + fixed_prompt_overhead(provider)
output_tokens  = input_tokens × 0.18
estimated_cost = input_tokens × in_rate + output_tokens × out_rate
```

The 0.18 output multiplier is a static heuristic chosen because the JSON response (a list of `{page, start, end, kind, confidence}` objects) is dominated by spans and short kind labels, empirically far smaller than the input. This factor is fixed for v1.0; if calibration drifts in real use, the value can be revisited but the formula stays static rather than adaptive.

For Ollama, cost is `$0.00`. The pre-run line shows a wall-clock estimate computed from the `tokens/sec` measured by the most recent `kuroi doctor` test request.

For batch runs (`--out-dir`, glob inputs), per-file estimates are summed and the aggregate is shown before the typed-yes prompt at the threshold (`>$1.00` triggers typed-yes per the design's safety nets).

After the run, the audit log records actual token usage. If `actual_cost / estimated_cost > 2.0`, kuroi prints a `note: cost estimate diverged from actual; consider reporting at https://kuroi.tools/docs/calibration` so the multiplier can be tuned over time.

### 1.2 `--seed` semantics

`--seed N` is a request, not a guarantee. The contract per provider:

| Provider | Seed parameter | Temperature | Reproducibility |
|---|---|---|---|
| Ollama | `seed=N` sent | `0` sent | Full, within model snapshot |
| OpenAI (when added) | `seed=N` sent | `0` sent | Bounded by `system_fingerprint` |
| Anthropic | not exposed by SDK | `0` sent | Best-effort only |

For each chunk request, the audit log records `temperature`, `seed_requested`, `seed_honored` (bool), `model_version`, `system_fingerprint` (when returned), `prompt_sha256`, `response_sha256`. Two runs can be diffed by comparing `response_sha256` per chunk.

If `--seed` is set against a provider that doesn't honor it, kuroi prints (in default verbosity):

```
note: --seed recorded in audit but only temperature=0 is enforced for this provider
```

This matches design principle 6 ("the audit log records honestly") and unblocks the v1.1+ `kuroi audit replay` feature without committing to it now.

### 1.3 Telemetry

Telemetry is removed from the design entirely.

- No telemetry endpoint, no schema file, no `kuroi telemetry` command.
- No `[telemetry]` section in config.
- No telemetry step in the `kuroi init` wizard.

The design's "kuroi never sees your documents" framing is preserved (and strengthened): there is no opt-in path that would change that. Future product needs may revisit, but it is out of scope for this spec.

## Cluster 2 — State persistence

### 2.1 Per-finding audit log schema

The audit file is NDJSON, one file per `kuroi run` invocation, named `<ISO-8601-Z>.jsonl`, in `~/.local/share/kuroi/audit/` (configurable). Created mode `0600`, directory mode `0700`.

Every line carries a discriminator field `event` so each line is self-identifying. Four line types appear in this order:

1. `event: "session_start"` — exactly one (line 1).
2. `event: "chunk_request"` — zero or more, one per LLM API call.
3. `event: "finding"` — zero or more, one per redaction.
4. `event: "session_end"` — exactly one (last line).

**`event: "session_start"` — session header.** Full provenance:

```json
{
  "event": "session_start",
  "audit_schema_version": 1,
  "session_id": "8f3c1c0e-5a4e-4b7d-9e2a-1b6c0e8a4d92",
  "ts_start": "2026-04-29T14:22:13.041Z",
  "kuroi_version": "0.4.2",
  "user": "pierre@icij",
  "host": "laptop-pierre.local",
  "input_path": "sources-meeting-notes.pdf",
  "input_sha256": "3a7f...c812",
  "input_pages": 12,
  "input_bytes": 1233920,
  "output_path": "redacted.pdf",
  "provider": "anthropic",
  "model": "claude-opus-4-7",
  "model_version": "claude-opus-4-7@2026-04-15",
  "system_fingerprint": null,
  "seed_requested": null,
  "rules": [{"name": "pii", "version": "3.2"}],
  "instructions": [{"text": "...", "kind": "redaction", "required": true}],
  "rule_set_files": [],
  "config_resolved_from": ["flag", "env:KUROI_PROVIDER", "user_config"]
}
```

**`event: "chunk_request"` — per-LLM-call records.** One per request to the model (multiple per session if chunking is used). Captures inputs and outputs needed to detect divergence between supposedly-reproducible runs.

```json
{
  "event": "chunk_request",
  "ts": "2026-04-29T14:22:14.118Z",
  "chunk_idx": 0,
  "pages": [1, 2, 3, 4, 5, 6, 7, 8],
  "temperature": 0,
  "seed_requested": null,
  "seed_honored": false,
  "system_fingerprint": null,
  "prompt_sha256": "b7a2...",
  "response_sha256": "4c1f...",
  "tokens_in": 3204,
  "tokens_out": 891,
  "duration_ms": 12041
}
```

**`event: "finding"` — per-redaction records.**

```json
{
  "event": "finding",
  "ts": "2026-04-29T14:22:13.041Z",
  "page": 4,
  "word_start": 5,
  "word_end": 6,
  "bbox": [142.1, 318.4, 198.7, 332.0],
  "text_sha256": "9f3c...",
  "text_length": 10,
  "kind": "person_name",
  "confidence": "high",
  "source": "instruction:0",
  "decision": "applied",
  "reviewer": "auto",
  "context_sha256": "1a4d..."
}
```

`source` is one of `"rule:<set>.<category>"` (e.g., `rule:pii.iban`), `"instruction:<idx>"` (1-based index into the session's instruction array), or `"exclusion:<idx>"`.

`decision` is `applied`, `rejected`, or `excluded`. `reviewer` is `auto` or `interactive`.

`text_sha256` is the SHA-256 of the redacted text. `context_sha256` is the SHA-256 of a 32-word window centered on the redaction. **The plaintext is not stored.** A reviewer who needs to inspect the redacted text reconstructs it from the original PDF (preserved in the backup) using `(page, word_start, word_end)`; the hash proves the audit references the same span.

Plaintext storage is opt-in for legal teams who need self-contained exhibit binders:

```toml
[audit]
include_text = true   # default false
```

When enabled, two extra fields are added to each finding event: `text` and `context`.

**`event: "session_end"` — session footer.**

```json
{
  "event": "session_end",
  "ts_end": "2026-04-29T14:22:31.441Z",
  "status": "ok",
  "redactions_applied": 47,
  "redactions_rejected": 0,
  "redactions_excluded": 2,
  "duration_ms": 18400,
  "tokens_in": 17204,
  "tokens_out": 2891,
  "cost_usd": 0.043,
  "verify_result": "pass",
  "verify_leak_count": 0,
  "output_sha256": "e91a...4fd0"
}
```

`status` is `ok`, `failed`, or `verify_failed`. `verify_result` is `pass`, `fail`, or `skipped`. On verification failure, the output is not written; the footer captures the leak count and the audit log is still complete.

### 2.2 First-time state tracking

State lives in `$XDG_STATE_HOME/kuroi/state.toml` (default `~/.local/state/kuroi/state.toml`), mode `0600`. Schema:

```toml
schema_version = 1
cloud_acknowledged = false
cloud_acknowledged_at = ""    # ISO-8601 when set
training_wheels_remaining = 3 # decrements per real run; 0 = wheels off
total_runs = 0
```

Atomic writes (tmp + rename), reusing the primitive already used for config writes.

Why a separate file from `config.toml`: config is "what the user has set"; state is "what kuroi knows about the user". Mixing them is hostile to people editing config (`vim ~/.config/kuroi/config.toml` should be safe).

`kuroi config reset --privacy` clears `cloud_acknowledged` and resets `training_wheels_remaining` to 3.

### 2.3 Backup cleanup mechanism

Lazy sweep: every `kuroi run` and `kuroi undo` invocation begins by walking the backup directory, parsing entry timestamp prefixes (the design's `2026-04-29T14-22-13Z` pattern, extended below), and removing anything older than `[backup] retention_hours`.

```toml
[backup]
retention_hours = 24   # positive integer; default 24
```

Valid values:

- positive integer — hours to keep
- `0` — keep forever (legal-hold workflows)

Negative values are rejected at config load with: `kuroi requires backups for in-place edits; set retention_hours to a positive integer or 0`.

The lazy sweep only inspects entries whose names match `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-[0-9a-f]{6})?/$`. Anything else (a user dropped files in the backup folder manually) is left alone.

Silent on success; under `-v`: `pruned N expired backups`.

The "user runs once and never returns" case is acceptable: backups sit on disk in their named subfolder until the user deletes the folder. No worse than the originals already on their filesystem, and the directory location was chosen with the user's confirmation in `kuroi init`.

`kuroi backups list` shows what's there with ages; `kuroi backups gc` runs the sweep on demand. Both have `--max-age` for inspection at non-default thresholds.

### 2.4 `kuroi review` session file location

Sessions live in `$XDG_STATE_HOME/kuroi/sessions/<input_sha256>.session.json`. JSON (mutable single object), not NDJSON.

```json
{
  "schema_version": 1,
  "input_sha256": "3a7f...c812",
  "input_path_at_start": "/Users/pierre/sources-meeting-notes.pdf",
  "started_at": "2026-04-29T13:55:02Z",
  "last_activity_at": "2026-04-29T13:58:14Z",
  "total_findings": 47,
  "decisions": [
    {"finding_idx": 0, "decision": "applied", "reviewer_note": ""},
    {"finding_idx": 1, "decision": "rejected", "reviewer_note": "false positive — public figure"}
  ]
}
```

Resume command: `kuroi review <pdf>`. kuroi hashes the bytes, looks up the session by hash. The user can move or rename the PDF and resume still works.

If the bytes differ from when the session started (`input_sha256` mismatch), kuroi prompts: `original differs from when the session started; start fresh? [y/N]`.

Sessions follow the same retention as backups (default 24h); same lazy sweep cleans them up. A session whose backup is expired is also expired.

## Cluster 3 — File handling and UX

### 3.1 Drag-and-drop path normalization

Runs only on **interactive path prompts** (guided mode, `kuroi init`). CLI arguments go through normal shell expansion and are not touched.

Pipeline:

1. Strip leading/trailing whitespace.
2. If wrapped in matching `'...'` or `"..."`, strip one layer of quotes.
3. If starts with `file://`, decode percent-escapes and strip the prefix.
4. If the input did not contain any quotes after step 2 (i.e., the original was either wrapped in matching quotes or had none at all), replace shell-style backslash escapes (`\ `, `\(`, `\)`, `\[`, `\]`, `\&`, `\$`) with the unescaped character. If the input still contains quote characters at this point, skip this step — the path likely has a literal quote and applying backslash unescape would corrupt it.
5. Expand `~` to home dir.
6. Resolve to absolute path.

Then validate the path exists and the suffix is `.pdf`. If validation fails, fall through to "did you mean" suggestions over the parent directory.

Test fixtures cover paste shapes from macOS Terminal (single-quoted), iTerm2 (single-quoted), Windows Terminal (double-quoted or unquoted), GNOME Terminal (backslash-escaped or single-quoted).

Pathological paths — those containing literal quote characters or extreme unicode — may not normalize correctly. In those cases the user falls back to typing the path directly; the help text on the path prompt mentions this option (`drag and drop the file here, or type the path`).

### 3.2 `--in-place` and backup interaction

`--in-place` writes to the original path. **It always takes a backup.** There is no `--no-backup` flag.

This collapses an apparent ambiguity in the design (the example error message at line 1027 suggested `--in-place --backup` as if `--backup` were a real flag) into the design's own resolved principle: "the original is never overwritten without an explicit `--in-place` flag, and even then a 24-hour backup is taken first."

Users who want short retention set `[backup] retention_hours = 1`. Users who want immediate cleanup run `kuroi backups gc` after their run. Users who want zero backup are fighting the tool deliberately and are not supported.

### 3.3 `--overwrite` and `.v<N>` collision behavior

Without `--overwrite` and the output path exists: refuse, print the next available `.v<N>.pdf`. The `<N>` is computed by globbing `<base>.v*.pdf` in the parent directory and taking `max(N) + 1`. If no `.v*` siblings exist, suggest `.v2`.

```
✗ I won't overwrite redacted.pdf.

Try one of:
    kuroi run leak.pdf -o redacted.v2.pdf
    kuroi run leak.pdf -o redacted.pdf --overwrite
```

The suggestion is **only printed** — the user re-runs with the suggested path. kuroi does not auto-rename. Auto-renaming creates "which file did I just write?" ambiguity that is worse than the friction of one extra command.

With `--overwrite`: silently overwrite. Standard CLI semantics.

For batch (`--out-dir`): per-file collision check up-front before any work begins. If any output exists, refuse the entire batch with a list of conflicting paths unless `--overwrite`. Never half-overwrite.

### 3.4 Concurrent runs

No locking on input — PyMuPDF opens read-only by default and reads are safe to parallelize.

Output is locked. Before writing, kuroi creates `<output>.kuroi.lock` via `O_CREAT|O_EXCL`. If the lock exists:

```
✗ another kuroi run is writing to redacted.pdf
  if that's wrong, delete redacted.pdf.kuroi.lock
```

The lock is removed on clean exit and on `SIGINT`/`SIGTERM` handlers. Stale locks are not auto-cleaned — they signal a real problem (crash, force-kill) and the user clears them deliberately.

Backup directory names include 6 random hex characters: `YYYY-MM-DDTHH-MM-SSZ-<random6>/`. Collisions are impossible even sub-second. The lazy sweep continues to work because the timestamp prefix is what gets parsed.

For batch (`--out-dir`): lock per output file. Two batches with disjoint outputs run concurrently fine; overlapping outputs surface the lock conflict on the first overlap.

## Cluster 4 — CLI surfaces

### 4.1 `kuroi diff` output format

A single canonical diff structure renders in three formats for v1.0; a fourth is deferred to v1.1+.

| Format | v1.0 | When to use |
|---|---|---|
| `--format text` (default) | yes | Terminal review, paginatable summary |
| `--format html` | yes | Self-contained file for email/case attachments |
| `--format json` | yes | Pipelines, NDJSON-per-page |
| `--format pdf` | v1.1+ | Side-by-side annotated PDF for exhibit binders |

Constraints:

- `text` is TTY-friendly; goes to stdout.
- `html` is single-file with inline CSS, no external assets.
- `json` is NDJSON, one line per page: `{page, before_text, after_text, redactions: [{bbox, kind, replacement}]}`.
- `html` requires `-o <path>` or stdout redirection; refuses to write to a TTY.

The deferred `pdf` format is the most work (PyMuPDF page stitching with consistent layout) and is the format a legal team actually wants. Tracked separately for v1.1+.

### 4.2 `kuroi models` output

```
Anthropic                                                       cloud
  claude-opus-4-7        (default)   $15.00 / $75.00 per Mtok   in: 200k
  claude-sonnet-4-6                  $3.00  / $15.00 per Mtok   in: 200k
  claude-haiku-4-5                   $0.80  / $4.00  per Mtok   in: 200k
  seed support: temperature=0 only (best-effort, recorded in audit)

OpenAI                                                          cloud
  gpt-5                              $X.XX  / $XX.XX per Mtok   in: NNNk
  seed support: full (system_fingerprint-bounded)

Ollama                                                          local
  llama3.1:70b           (installed) free                       in: 128k
  llama3.1:8b            (installed) free                       in: 128k
  seed support: full

Default: anthropic / claude-opus-4-7   (configurable)
Pricing last updated: 2026-04-15  (refresh: kuroi config refresh-pricing)
```

- Provider header line includes privacy posture (`cloud` / `local`).
- Per-model: name, default flag, input/output cost per million tokens, context window size, install status (Ollama only).
- Per-provider seed support note.
- Footer: configured default and pricing-data freshness.
- `kuroi models <provider>` filters to one provider.
- `--json` for machine-readable.
- For Ollama: queries the running daemon for installed models. If the daemon is down, prints `Ollama daemon not running — see kuroi doctor` and lists kuroi-known models without install state.

There is no `kuroi models pull` subcommand. Users install Ollama models with `ollama pull <name>` directly. The `kuroi init` wizard already handles the first-time install.

### 4.3 `-v` vs `-vv` boundary

| Output | default | `-v` | `-vv` |
|---|---|---|---|
| Plain-English progress, cost line, breakdown | ✓ | ✓ | ✓ |
| Full list of findings before confirmation | — | ✓ | ✓ |
| Token counts per chunk | — | ✓ | ✓ |
| Per-rule fire counts (regex matched N, LLM matched M) | — | ✓ | ✓ |
| LLM request/response sizes (bytes) | — | ✓ | ✓ |
| LLM reasoning text when provider returns it | — | ✓ | ✓ |
| Full prompt sent to LLM | — | — | ✓ |
| Full LLM response | — | — | ✓ |
| Per-request HTTP timing | — | — | ✓ |
| Internal state transitions | — | — | ✓ |
| Stack traces on errors | — | — | ✓ |

`-q` suppresses everything except the final result line and errors.

`--json` is independent of verbosity: stdout gets JSON only; logs at the chosen level go to stderr.

`-vv` may print sensitive content because the full prompt contains document text. The first `-vv` invocation per process prints, once:

```
note: -vv prints document text to stderr; redirect if recording
```

This is a per-process warning, not stored anywhere — opening a new terminal earns a fresh warning. The warning never appears under `--json` (the user is already aware they're capturing output).

## Cluster 5 — OCR and validation

### 5.1 OCR redaction bbox precision

OCR'd documents have noisy word bounding boxes; redactions must be pessimistic.

```toml
[ocr]
padding_px = 2          # PDF user-space units, applied to outside edges
min_confidence = 60     # Tesseract scale 0-100
```

Padding is added to the outside edges of word groups (multi-word redactions get the union rect, then padding on the outside only). Internal joins between words are not padded.

Words below `min_confidence` are not eligible for the LLM kind-detection pass — they're noise — but they are still scanned by `kuroi verify` (we cannot rely on the same OCR engine giving the same answer twice on different runs).

When the LLM proposes redacting a low-confidence word (because the model can recognize a noisy "S?rah" as a name in context), kuroi expands the redaction to the surrounding line's bbox. Pessimistic, covers more text than strictly needed. User-visible note in run output:

```
note: 1 low-confidence redaction expanded to line on page 3
```

For fully-rasterized pages, **pixel-level redaction** is the mode: kuroi rasterizes the page, draws true black rectangles over target bboxes, replaces the page with the rasterized+redacted version. Any original text layer on the page is also stripped. By construction there is no text under the boxes.

`kuroi verify` for OCR'd output runs OCR over each redaction region; if any text is recovered above `min_confidence`, it's flagged as a leak.

### 5.2 Custom-rule and instruction-file YAML schemas

Both formats are formally specced as Pydantic v2 models and validated on load. All models declare `model_config = ConfigDict(extra="forbid")` so unknown fields are a hard error — this is the allow-list mechanism that prevents typos from silently disabling rules and makes future schema additions a deliberate `schema_version` bump. Schema docs are auto-generated from the models and published in the docs site.

**Custom rule file** (loaded via `--rules custom:./path.yaml`):

```python
class CustomRuleFile(BaseModel):
    schema_version: int = 1
    name: str | None = None             # informational
    exact_match: list[str] = []
    regex: list[str] = []
    case_sensitive: bool = False        # default for exact_match
    semantic_hints: list[str] = []      # passed to LLM as priority instructions
```

- Each `regex` pattern is compile-validated at load. Failures raise with the YAML line number (PyYAML loader hooks).
- Empty rule files are allowed (starter templates).
- All custom-rule matches use `kind: "custom"` in the audit log. Users wanting finer granularity ship multiple rule files: `--rules custom:names.yaml,custom:ids.yaml`.

**Instruction file** (loaded via `--instruct-file <path>`):

```python
class Instruction(BaseModel):
    text: str
    kind: Literal["redaction", "exclusion"] = "redaction"
    required: bool = False              # zero matches → fail run

class InstructionScope(BaseModel):
    pages: list[int | str] = []         # int or "N-M" range
    exclude_pages: list[int | str] = []

class InstructionFile(BaseModel):
    schema_version: int = 1
    language: str | None = None         # ISO 639-1; auto-detected if absent
    instructions: list[Instruction] = []
    scope: InstructionScope = InstructionScope()
```

- `pages` items: positive integer or `"N-M"` range with `N <= M`. Validator expands ranges; invalid ranges raise with YAML line numbers.
- 1-based page numbering.
- If both `pages` and `exclude_pages` cover the same page, `exclude_pages` wins.
- At least one instruction required.

Tooling:

- `kuroi rules validate <path>` — validate a custom rule file.
- `kuroi run --validate-instruct-file <path>` — parse an instruction file without running.
- Both surface validation errors with file:line context.

### 5.3 `--corpus-mode` data flow

`--corpus-mode` is v1.1+. This spec does not commit to an implementation; it commits to **constraints any future implementation must honor** so the design isn't a blank check at v1.0 freeze.

Constraints:

1. **Per-document LLM calls only.** Documents are never joined into a single prompt. The corpus aspect is *instruction propagation*, not *data joining*.

2. **Cross-document state is small, structured, and allow-listed.** After processing document N, kuroi extracts a `CorpusState` object (e.g., `{discovered_entities: [{kind, canonical_form}], discovered_aliases: {canonical: [variants]}}`) — never raw document text. The schema uses Pydantic with `extra="forbid"` and string-length caps on all string fields; document text cannot leak between requests because the schema rejects unknown fields and bounds the per-field byte budget.

3. **State propagates as priority instructions to document N+1.** Same channel as user-supplied instructions; same `<document>`-tag isolation in the prompt.

4. **Order is deterministic.** Sorted by file path lexicographically by default; `--corpus-order <strategy>` for explicit ordering (e.g., chronological by filename pattern).

5. **Audit captures the corpus state at each step.** Reviewable post-hoc; replay possible if seed-honoring providers are used end-to-end.

6. **Privacy review is a schema review.** The corpus-state schema *is* the privacy boundary; reviewers verify the schema, not the implementation. Anything not in the schema cannot leak between requests.

Net effect: a leak in one cross-document request reveals the entities discovered so far, not the documents.

## What's still deferred

After this spec, the following are still v1.1+ and have their own design work ahead:

- The Textual review TUI (`kuroi review`).
- `kuroi audit replay`.
- `--corpus-mode` implementation (constraints fixed in 5.3, implementation TBD).
- `--strict-instructions` mode.
- `--refine` interactive instruction loop.
- `--format pdf` for `kuroi diff`.
- Multilingual rule sets (`pii-fr`, `pii-de`, `pii-ru`).
- `kuroi rules publish` and any community rule registry.

## Minimum dependency additions implied by this spec

The implementation work that follows from this spec adds these to `pyproject.toml`:

- `pydantic` v2 (already implicit; pin and confirm).
- No new mandatory deps. `pydantic-settings`, `structlog`, `Textual`, `Questionary`, `tenacity`, `ocrmypdf`, `pytesseract`, `openai` — all listed in the parent design — remain pending and are added by the specs that introduce the features needing them.

## Open items not addressed by this spec

These design questions exist but are not in scope for this spec — they need their own design passes:

- The full `kuroi review` TUI design (Textual app structure, key bindings, accessibility).
- Rule sharing and signed publishers (item 4 in `kuroi-design.md` open questions).
- Whether guided mode stays on by default (item 5).
- Training-wheels duration tuning (item 6) — three runs is a guess; this spec preserves the design's three-run default but tuning belongs to a user-study follow-up.

## Acceptance criteria

This spec is "done" when the implementation plan it generates can be executed without further design re-decisions for the 17 gaps it covers. If a future implementer hits one of the 17 areas and asks "what should this do," the answer is in this document and is precise enough to implement against without rediscovery.
