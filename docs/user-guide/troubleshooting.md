# Troubleshooting

Common failures and how to fix them. Run `kuroi doctor` first — it checks
most of these in one go.

## `kuroi doctor` output

```sh
$ kuroi doctor
✓ Python 3.12.4
✓ Provider: anthropic (model: claude-opus-4-7)
✗ ANTHROPIC_API_KEY not set
✓ Configuration directory: ~/.config/kuroi/
```

The first failing line tells you what to fix.

## "ANTHROPIC_API_KEY not set"

```sh
$ export ANTHROPIC_API_KEY=sk-ant-...
```

Persist by adding the export to your shell rc file. Or switch to Ollama
for offline runs (see [LLM providers](providers.md)).

## "File is locked" / `LockTimeout`

kuroi uses an advisory lock file alongside each input PDF to prevent two
runs from racing on the same file. If a previous run crashed without
releasing the lock, remove the stray `<pdf>.kuroi.lock` file:

```sh
$ rm path/to/document.pdf.kuroi.lock
```

## Provider rate limits

If you see `429 Too Many Requests` from Anthropic, lower `--workers` and
re-run; kuroi resumes where it stopped.

## "PDF is too large to extract"

Very large PDFs (hundreds of megabytes / thousands of pages) may exceed
PyMuPDF's word-extraction limits. Split the file with
`pymupdf.open(...).save(..., from=, to=)` or `pdftk`, redact each part,
then re-merge.

## "Verification gate failed"

`kuroi verify` re-runs the regex rules over a redacted output. If it
flags a match, the original redaction missed it. Re-run with `--seed`
fixed and `-vv` to see the LLM's reasoning, then file an issue with the
audit record attached.

## Where logs live

- **Audit records (per finding):** `~/.local/state/kuroi/audit/<hash>/<run>.jsonl`
- **Backups (full PDFs):** `~/.local/state/kuroi/backups/`
- **State (resume info):** `~/.local/state/kuroi/state/`
- **Config:** `~/.config/kuroi/config.toml`

`-v` (info) and `-vv` (debug) on any command print a runtime trace to
stderr.

## Still stuck?

Open an issue at [github.com/ICIJ/kuroi/issues](https://github.com/ICIJ/kuroi/issues)
with:

1. The command you ran.
2. `kuroi --version` and `kuroi doctor` output.
3. The relevant audit JSONL excerpt (with `audit_include_text=false` so
   you don't leak the data you're trying to redact).
