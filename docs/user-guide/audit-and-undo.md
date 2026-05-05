# Audit, diff, undo & backups

Every kuroi run is reversible and auditable. This page covers the four
commands you need to inspect, verify, restore, and clean up.

## See what changed: `kuroi diff`

`kuroi diff` takes the original and the redacted PDF and prints, per
page, the bounding box and before-text snippet of every redaction:

```sh
$ kuroi diff report.pdf report.redacted.pdf
Page 3: 2 redactions
  - [40,120,180,138]  'j.doe@example.com'
  - [200,400,310,418]  '+33 6 12 34 56 78'
Page 7: 1 redaction
  - [60,210,220,230]  'Jane M. Doe'
```

Use `--format json` for one JSON record per page (machine-readable),
or `--format html -o diff.html` for a side-by-side view. Pass
`-o <path>` with any format to write to a file instead of stdout.

## Re-check a redacted PDF: `kuroi verify`

`verify` runs the same regex rules over the redacted output to catch
anything that slipped through:

```sh
$ kuroi verify report.redacted.pdf
  PASS  report.redacted.pdf: no residual leaks
```

If it finds anything, the exit code is non-zero and the leaked spans are
listed. Wire `kuroi verify` into your batch pipeline as a gate.

## Restore the original: `kuroi undo`

```sh
$ kuroi undo
  Last backup: 2026-04-30T09-31-02Z-a1b2c3
  Will restore: /home/you/work/report.pdf
Restore now? [Y/n]: y
  Restored.
```

`undo` takes no positional argument: it restores the most recent backup
in the configured backup directory (default
`$XDG_DATA_HOME/kuroi/backups/`, falling back to
`~/.local/share/kuroi/backups/` when the env var is unset; override with
`--backup-dir`). The backup itself is retained until it falls outside the
retention window.

## List & garbage-collect backups

Each backup is a timestamped subdirectory of the backup root containing a
`manifest.json` and the original PDF.

```sh
$ kuroi backups list
  2026-04-28T08-00-01Z-44ab12  49h ago  /home/you/finance/invoice.pdf
  2026-04-29T14-15-22Z-9f0e21  19h ago  /home/you/work/report.pdf
  2026-04-30T09-31-02Z-a1b2c3  0h ago   /home/you/work/report.pdf
```

Rows are sorted by directory name (oldest first). Each row shows the
session directory name, an age in hours, and the original path recorded
in the manifest.

!!! warning "Destructive: review before running"
    `kuroi backups gc` permanently deletes backups older than `--max-age`
    hours (default: 24). There is no preview / dry-run flag yet; run
    `kuroi backups list --root <dir>` first to see what would be eligible,
    or pass `--max-age 0` to disable pruning.

```sh
$ kuroi backups gc --max-age 24
  Pruned 1 backup.
```

To change the default retention window globally, edit
`~/.config/kuroi/config.toml` and set:

```toml
[backup]
retention_hours = 168   # one week
```

`retention_hours = 0` keeps every backup (legal-hold mode).

## Where audit logs live

Each run writes a JSONL record to:

```
~/.local/share/kuroi/audit/<timestamp>.jsonl
```

The directory is configurable with `kuroi run --audit-dir <path>`; the
filename is the same timestamp string used for the matching backup
(e.g. `2026-04-30T09-31-02Z-a1b2c3.jsonl`). Each file contains a
`session_start` header, one `finding` line per applied redaction, and a
`session_end` footer with the verification status. The dataclasses for
each line live in `src/kuroi/core/audit_records.py`.

Set `include_text = true` under `[audit]` in
`~/.config/kuroi/config.toml` to capture matched snippets in the audit
log (off by default — opt-in because audit logs themselves can be
sensitive):

```toml
[audit]
include_text = true
```

## Next steps

- [Troubleshooting](troubleshooting.md) — when `kuroi verify` flags a leak.
- [Configuration](../reference/config.md) — every audit/backup setting.
