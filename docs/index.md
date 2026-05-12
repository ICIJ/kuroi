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

    Install via `pip`, `pipx`, or `uv` on Python 3.12+. Takes under a minute on a clean machine.

- :material-rocket-launch: __[Quick start](user-guide/quickstart.md)__

    Pick a provider, redact your first PDF end-to-end, and learn the core `run` / `diff` / `undo` loop.

- :material-book-open-variant: __[Use as a library](developer-guide/library-usage.md)__

    Drop kuroi into a Python pipeline with the stable public API — detectors, findings, and the redaction writer.

- :material-backup-restore: __[Audit & undo](user-guide/audit-and-undo.md)__

    Every run writes a JSONL audit log and a full backup, so you can diff, verify, and roll back at any time.

</div>
