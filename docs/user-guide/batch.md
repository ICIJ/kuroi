# Batch redaction

The `kuroi run` command processes one PDF per invocation. To process many,
loop over the inputs from your shell. This page shows the recipes for the
common batch shapes.

## Loop over a folder

```sh
$ for pdf in inbox/*.pdf; do
    kuroi run "$pdf" -o "redacted/$(basename "$pdf" .pdf).redacted.pdf"
  done
```

`-o` lets you place each output under a sibling tree. Use `--overwrite` if
the destination may already exist.

## Skip files that are already redacted

`kuroi run` writes a backup before redacting and leaves the redacted output
in place. Re-running over the same input rewrites it. To avoid that in a
loop, test the output path first:

```sh
$ for pdf in inbox/*.pdf; do
    out="redacted/$(basename "$pdf" .pdf).redacted.pdf"
    [ -e "$out" ] && continue
    kuroi run "$pdf" -o "$out"
  done
```

## Recovering from interruption

If a loop is interrupted, the pre-redaction backup of every file kuroi
already started is in `~/Documents/kuroi-backups/`. Use `kuroi backups list`
to inspect them, then re-run the loop — the test above will skip files
whose output was completed.

## Cost considerations

Each `kuroi run` invocation against Anthropic spends tokens. There is no
built-in dry-run estimator today; budget by sampling a few representative
PDFs first. To stay free of metered cost, switch to local Ollama —
[LLM providers](providers.md).

## Worked example

```sh
$ export ANTHROPIC_API_KEY=sk-ant-...
$ mkdir -p redacted
$ for pdf in invoices/Q1/*.pdf; do
    out="redacted/$(basename "$pdf" .pdf).redacted.pdf"
    [ -e "$out" ] && { echo "skip $pdf"; continue; }
    kuroi run "$pdf" -o "$out" || break
  done
```

The `|| break` aborts the loop on the first failure; remove it if you
want to push through and review failures afterwards.

## Next steps

- [LLM providers](providers.md) — switch to free, local Ollama.
- [Audit & undo](audit-and-undo.md) — review individual files.
