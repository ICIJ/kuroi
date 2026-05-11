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
$ kuroi run report.pdf -o report.redacted.pdf
  Estimated cost: $0.0042  (3219 input tokens, anthropic/claude-opus-4-7)
  Found 47 candidate redactions.
Apply redactions? [Y/n]: y
  Wrote report.redacted.pdf
  Backup: ~/.local/share/kuroi/backups/2026-04-30T09-31-02Z-a1b2c3/report.pdf
  Audit: ~/.local/share/kuroi/audit/2026-04-30T09-31-02Z-a1b2c3.jsonl
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
