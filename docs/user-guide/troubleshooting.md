# Troubleshooting

Common failures and how to fix them. Run `kuroi doctor` first — it checks
most of these in one go.

## `kuroi doctor` output

```sh
$ kuroi doctor
  kuroi version 0.1.0                                                        ok
  Python version 3.12.4                                                      ok
  Anthropic API key                ANTHROPIC_API_KEY is not set; cloud redaction is unavailable  PROBLEM
  tesseract                        tesseract not found in PATH (optional for v0.1)               warn
  qpdf                             /usr/bin/qpdf                                                 ok
  Provider                         anthropic                                                     ok
  Model                            claude-opus-4-7                                               ok

One or more checks failed. See messages above.
```

Each line shows a label, a detail message, and a status (`ok`, `warn`,
or `PROBLEM`). A `PROBLEM` exits non-zero; `warn` is informational. Fix
the highest-listed `PROBLEM` first.

## "ANTHROPIC_API_KEY not set"

```sh
$ export ANTHROPIC_API_KEY=sk-ant-...
```

Persist by adding the export to your shell rc file. Or switch to Ollama
for offline runs (see [LLM providers](providers.md)).

## "File is locked" / `LockHeldError`

kuroi creates an advisory lock file alongside each redacted **output** to
prevent two runs from racing on the same destination. The lockfile path
is the output path with `.kuroi.lock` appended. For an output written to
`document.redacted.pdf`, the lock is `document.redacted.pdf.kuroi.lock`.

If a previous run crashed without releasing the lock, the next run will
fail with a message that names the stray file. Delete it manually:

```sh
$ rm path/to/document.redacted.pdf.kuroi.lock
```

Stale locks are intentionally not auto-removed: they're a real signal of
a prior crash, and the user clears them deliberately.

## Provider rate limits

If you see `429 Too Many Requests` from Anthropic, wait a moment before
retrying. `kuroi run` processes one PDF per invocation, so when you're
batching in a shell loop, sleep between iterations or break the input
list into smaller chunks.

## "PDF is too large to extract"

Very large PDFs (hundreds of megabytes / thousands of pages) can exceed
PyMuPDF's word-extraction limits. Split the file at the shell level
first, then redact each part:

```sh
$ qpdf --split-pages large.pdf parts.pdf
$ for part in parts*.pdf; do kuroi run "$part" -o "${part%.pdf}.redacted.pdf"; done
```

Re-merge the redacted parts with
`qpdf --empty --pages parts*.redacted.pdf -- merged.pdf`.

## "Verification gate failed"

`kuroi run` calls `kuroi verify` on its own output before writing it; if
the verifier finds residual sensitive text, the run aborts and nothing is
written to the destination. To reproduce a problematic run with a fixed
seed and full request/response logs:

```sh
$ kuroi -vv run --seed 42 input.pdf -o input.redacted.pdf
```

`-v`/`-vv` are top-level flags on the `kuroi` command and must come
before the subcommand. `--seed` is a flag on `kuroi run` (not on
`kuroi verify`); `verify` takes only the PDF path. Then re-run
`kuroi verify <output>` to see exactly which spans the verifier flagged.

## Where logs live

- **Audit records (per finding):** `~/.local/share/kuroi/audit/<timestamp>.jsonl`
  — one JSONL file per run. Configurable with `kuroi run --audit-dir <path>`.
- **Backups (full PDFs):** `~/.local/share/kuroi/backups/<timestamp>/<filename>.pdf`
  (or `$XDG_DATA_HOME/kuroi/backups/...` when the env var is set) — one
  timestamped subdirectory per run. Configurable with
  `kuroi run --backup-dir <path>` (and `kuroi undo --backup-dir <path>`).
  Skip the backup copy with `kuroi run --no-backup`.
- **Config:** `~/.config/kuroi/config.toml` (or `$XDG_CONFIG_HOME/kuroi/config.toml`).

`-v` (info) and `-vv` (debug) on any command print a runtime trace to
stderr.

## Still stuck?

Open an issue at [github.com/ICIJ/kuroi/issues](https://github.com/ICIJ/kuroi/issues)
with:

1. The command you ran.
2. `kuroi --version` and `kuroi doctor` output.
3. The relevant audit JSONL excerpt (with `include_text = false` under
   `[audit]` so you don't leak the data you're trying to redact).
