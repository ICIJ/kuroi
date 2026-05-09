---
hide:
  - toc
---

# kuroi

Strip sensitive data from PDFs with LLM assistance.

kuroi is a command-line tool that removes personally identifiable information
(PII) and other sensitive content from PDF files. It combines deterministic
regex rules with an LLM judge so that names, addresses, and other contextual
identifiers can be detected even when they don't match a fixed pattern. Every
run produces an auditable record and a backup, so you can verify what was
removed and restore the original at any time.

## 30-second demo

```sh
$ export ANTHROPIC_API_KEY=sk-ant-...
$ kuroi run report.pdf
✓ Detected 47 findings across 12 pages (32 regex, 15 llm)
✓ Wrote redacted file: report.redacted.pdf
✓ Backup at: ~/.local/share/kuroi/backups/report-20260430-093102.pdf
```

## Get started

<div class="grid cards" markdown>

- :material-download: __[Install](user-guide/install.md)__

    Get kuroi onto your machine in under a minute.

- :material-rocket-launch: __[Quick start](user-guide/quickstart.md)__

    Redact your first PDF end-to-end.

- :material-book-open-variant: __[Use as a library](developer-guide/library-usage.md)__

    Integrate kuroi into your own Python pipeline.

</div>
