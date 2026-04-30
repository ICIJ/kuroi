# Batch redaction

Run kuroi over a folder of PDFs in one go, with cost estimation and
resumable state.

## Globbing a folder

```sh
$ kuroi run ./inbox/*.pdf
```

Or recursively:

```sh
$ kuroi run ./inbox/**/*.pdf
```

kuroi processes each file independently and prints a per-file progress
line. A summary is written at the end.

## Output resolution

By default, every redacted file is written next to the input as
`<name>.redacted.pdf`. Override the destination with `--output-dir`:

```sh
$ kuroi run ./inbox/*.pdf --output-dir ./redacted/
```

The directory tree of the inputs is preserved under `--output-dir`. If a
target file already exists, kuroi refuses to overwrite it unless you pass
`--force`.

## Cost estimation

Before running a large batch against a paid provider, dry-run the cost:

```sh
$ kuroi run ./inbox/*.pdf --estimate
Estimated cost: $0.84 across 50 files (claude-opus-4-7 @ ICIJ pricing)
Run again without --estimate to proceed.
```

The estimate uses the per-token pricing baked into `kuroi.core.pricing`
(refresh it with `kuroi config refresh-pricing`).

## Resuming an interrupted batch

If a batch is interrupted (`Ctrl-C`, network drop, machine restart),
re-running the same command picks up where it left off. State is kept in
`~/.local/state/kuroi/state/` keyed by input file content hash, so a
half-finished file is re-attempted from scratch but completed files are
skipped.

```sh
$ kuroi run ./inbox/*.pdf
[3/50] invoice-april.pdf .......... ✓ (cached, skipped)
[4/50] invoice-may.pdf ............ running ...
```

## Parallelism

Process multiple files concurrently with `--workers`:

```sh
$ kuroi run ./inbox/*.pdf --workers 4
```

Default is 1 (sequential). Increase cautiously when using a paid provider
— concurrent requests multiply your spend.

## Worked example: 50 quarterly invoices

```sh
$ export ANTHROPIC_API_KEY=sk-ant-...
$ kuroi run ./invoices/Q1/*.pdf --output-dir ./redacted/Q1/ --estimate
Estimated cost: $0.84

$ kuroi run ./invoices/Q1/*.pdf --output-dir ./redacted/Q1/ --workers 4
[1/50] inv-001.pdf ................ ✓ (3.2s)
...
[50/50] inv-050.pdf ............... ✓ (2.9s)

Summary: 50/50 succeeded, 412 findings, 0 errors, $0.81 spent
```

## Next steps

- [LLM providers](providers.md) — switch to free, local Ollama for batches.
- [Audit & undo](audit-and-undo.md) — review the diff for one file in the batch.
