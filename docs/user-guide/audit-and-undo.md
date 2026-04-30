# Audit, diff, undo & backups

Every kuroi run is reversible and auditable. This page covers the four
commands you need to inspect, verify, restore, and clean up.

## See what changed: `kuroi diff`

```sh
$ kuroi diff report.pdf
report.pdf  →  report.redacted.pdf

  page 3, words 12-13   email          anthropic    high      "j.doe@example.com"
  page 3, word 88       phone          rules:pii-en high      "+33 6 12 34 56 78"
  page 7, words 22-25   person_name    anthropic    medium    "Jane M. Doe"
  ...

47 findings (32 regex, 15 llm)
```

`--include-text` and `--exclude-text` filter the listing. Use `--json`
for machine-readable output.

## Re-check a redacted PDF: `kuroi verify`

`verify` runs the same regex rules over the redacted output to catch
anything that slipped through:

```sh
$ kuroi verify report.redacted.pdf
✓ No regex matches detected
```

If it finds anything, the exit code is non-zero and the leaked spans are
listed. Wire `kuroi verify` into your batch pipeline as a gate.

## Restore the original: `kuroi undo`

```sh
$ kuroi undo report.pdf
✓ Restored report.pdf from backup report-20260430-093102.pdf
```

`undo` finds the most recent backup matching the input filename and
copies it back over the redacted output. The backup is retained.

## List & garbage-collect backups

```sh
$ kuroi backups list
report-20260430-093102.pdf   124 KB    today, 09:31
report-20260429-141522.pdf   118 KB    yesterday
invoice-20260428-080001.pdf   42 KB    2 days ago
```

!!! warning "Destructive: review before running"
    `kuroi backups gc` permanently deletes backups older than the retention
    window (default: 24h). Pass `--dry-run` first to preview.

```sh
$ kuroi backups gc --dry-run
Would delete 3 backups (oldest: 2 days ago)

$ kuroi backups gc
Deleted 3 backups, 318 KB freed.
```

Tune retention with:

```sh
$ kuroi config set backup_retention_hours 168   # one week
```

## Where audit logs live

Each run writes a JSONL record to:

```
~/.local/state/kuroi/audit/<input-hash>/<run-id>.jsonl
```

One line per finding. The schema lives in
`src/kuroi/core/audit_records.py`. Use
`kuroi config set audit_include_text true` if you want the matched
snippets stored in the audit log (off by default — opt-in because audit
logs themselves can be sensitive).

## Next steps

- [Troubleshooting](troubleshooting.md) — when `kuroi verify` flags a leak.
- [Configuration](../reference/config.md) — every audit/backup setting.
