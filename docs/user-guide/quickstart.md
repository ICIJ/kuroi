# Quick start

Redact your first PDF in under five minutes.

## 1. Set your provider key

kuroi defaults to Anthropic. Export your API key once per shell, or add it
to your shell rc file:

```sh
export ANTHROPIC_API_KEY=sk-ant-...
```

If you want to stay fully offline, use [Ollama](providers.md#ollama)
instead — no API key required.

## 2. Run the redactor

```sh
$ kuroi run report.pdf
```

kuroi prints a live-updating Rich progress display:

```
[1/1] report.pdf
  Extracting words ........ done (12 pages, 4,832 words)
  Applying regex rules .... 32 findings
  LLM judge ............... 15 findings
  Writing redacted PDF .... ✓
  Backup .................. ~/.local/state/kuroi/backups/report-20260430-093102.pdf
```

The redacted file is written next to the input as `report.redacted.pdf`
by default. See [Batch redaction](batch.md) for output-resolution rules.

## 3. Inspect the diff

To see what kuroi changed, run:

```sh
$ kuroi diff report.pdf
```

The diff shows each finding by page, category, and word range — colour-coded
by confidence (high / medium / low).

## 4. Restore if you need to

If anything looks wrong, restore the latest backup:

```sh
$ kuroi undo report.pdf
```

`kuroi undo` writes the original back into place. Backups are kept for
24 hours by default; tune the retention via [`kuroi config`](../reference/config.md).

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
