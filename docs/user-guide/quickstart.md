# Quick start

Redact your first PDF in under five minutes.

## 1. Set your provider key

kuroi defaults to Anthropic. Export your API key once per shell, or add it
to your shell rc file:

```sh
export ANTHROPIC_API_KEY=sk-ant-...
```

If you want to stay fully offline, use [Ollama](providers.md)
instead — no API key required.

## 2. Run the redactor

```sh
$ kuroi run report.pdf -o report.redacted.pdf
```

kuroi prints a cost estimate, asks you to confirm, then writes the
redacted output:

```
  Estimated cost: $0.0042  (3219 input tokens, anthropic/claude-opus-4-7)
  Found 47 candidate redactions.
Apply redactions? [Y/n]: y
  Wrote report.redacted.pdf
  Audit: ~/.local/share/kuroi/audit/2026-04-30T09-31-02Z-a1b2c3.jsonl
```

A copy of the original is saved under
`~/.local/share/kuroi/backups/2026-04-30T09-31-02Z-a1b2c3/report.pdf` so you
can roll back at any time.

The `-o` flag selects the output path. Use `--in-place` if you want
kuroi to overwrite the input. Either flag is required. See
[Batch redaction](batch.md) for output-resolution rules.

## 3. Inspect the diff

To see what kuroi changed, run `kuroi diff` with both the original and
redacted PDFs:

```sh
$ kuroi diff report.pdf report.redacted.pdf
Page 3: 2 redactions
  - [40,120,180,138]  'j.doe@example.com'
  - [200,400,310,418]  '+33 6 12 34 56 78'
Page 7: 1 redaction
  - [60,210,220,230]  'Jane M. Doe'
```

The text renderer prints each redaction as a bbox plus the before-text
snippet. Use `--format json` for machine-readable output, `--format html`
for a side-by-side view.

## 4. Restore if you need to

If anything looks wrong, restore the latest backup:

```sh
$ kuroi undo
```

`kuroi undo` restores the most recent backup in
`$XDG_DATA_HOME/kuroi/backups/` (default `~/.local/share/kuroi/backups/`).
Pass `--backup-dir` to point at a different directory. Backups are kept
for 24 hours by default; use `kuroi backups gc --max-age <hours>` to drop
old backups manually.

## What just happened?

1. kuroi ran the [PII rule pack](../developer-guide/writing-rule-packs.md)
   over the PDF text using regex.
2. Snippets the regex couldn't classify were sent to the configured LLM
   for context-aware judgement.
3. PyMuPDF rewrote the content stream so the redacted text is no longer
   recoverable, not just covered with a black box.
4. A full audit record was written so you can always trace why a span
   was redacted. See [Audit & undo](audit-and-undo.md).

## Next steps

- [Batch redaction](batch.md) — process a folder of PDFs.
- [LLM providers](providers.md) — switch to local-only Ollama.
- [Audit, diff, undo & backups](audit-and-undo.md) — verify and reverse.
