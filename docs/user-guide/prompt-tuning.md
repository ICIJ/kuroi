# Prompt tuning

kuroi sends each batch of pages to the LLM as a stream of word-indexed
tokens. By default, the prompt is a flat sequence:

```
<page n="1">
[0]Patient [1]Name [2]: [3]John [4]Doe
</page>
```

This is the simplest possible representation, and it works. But on
documents with strong layout structure — forms, tables, recurring
headers — the LLM sometimes struggles to tell a field label from its
value, or treats the same recurring footer differently across pages.

## `--layout-aware`

Pass `--layout-aware` (or set `[prompt] layout_aware = true` in your
config) to wrap each page's words in `<block>` tags reflecting
PyMuPDF's layout analysis:

```
<page n="1">
<block id="3">[0]Patient [1]Name [2]:</block>
<block id="4">[3]John [4]Doe</block>
</page>
```

The model still reports findings as `(page, start, end)` word ranges —
the response schema is unchanged. The block tags are advisory metadata
that the model uses to disambiguate which words belong together.

### When to enable

- **Forms with field labels next to values.** Patient records, intake
  forms, court filings.
- **Documents with recurring headers/footers.** Multi-page legal
  documents, financial reports.
- **Documents where the LLM has been mis-redacting structural cues**
  (column headers, signature lines, page numbers) as PII.

### When it doesn't help much

- **Dense narrative prose.** Court opinions, contract bodies. Layout
  structure adds little signal in long unbroken paragraphs.
- **OCR'd pages.** OCR typically produces a single block per page, so
  the layout-aware prompt collapses to the flat shape with one extra
  wrapper.

### Cost

Block tags add roughly 5% prompt-token overhead on a typical page. The
per-batch progress line shows `blocks=N` when layout-aware mode is on,
so you can see the cost in your own runs:

```
  Batch 4/12 (pages 16-20)... done in 1.2s, tokens_in=2871 tokens_out=412 blocks=12
```

### Current limits

This first ship covers paragraph-level block boundaries. Two related
improvements are tracked in separate design docs:

- **Table-aware overlay** (column/row tags inside `<block type="table">`)
  — particularly helpful for column-vs-name disambiguation in dense
  tables. Not yet shipped.
- **Cross-page recurring-element deduplication** — emit recurring
  headers/footers once and reference back on subsequent pages. Larger
  token-savings win for repetitive documents. Not yet shipped.
