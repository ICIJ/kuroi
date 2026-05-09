# Instruction Decomposition for Small Ollama Models

**Date:** 2026-05-09
**Status:** Draft

## Overview

Small local models (e.g. `llama3.1:8b`, `qwen2:7b`) routinely fail when handed
a long multi-rule `--instruct`. They time out, return malformed JSON, or
silently drop rules. This spec adds an **instruction decomposer** that, on
Ollama runs only, splits the user's instruction into atomic sub-rules and
dispatches one provider call per rule per page-batch.

A new module `core/instruction_decomposer.py` owns the logic:

1. A deterministic parser splits on numbering (`1.`), bullets (`-`, `*`),
   or blank-line-separated paragraphs.
2. If the parser returns one rule and the input exceeds 300 characters, a
   single LLM "split this" fallback dispatches against the same Ollama
   provider, expecting `{"rules": [...]}` JSON.
3. If both stages fail to produce ≥2 atomic rules, the original instruction
   is used as-is — best-effort, no regression versus today.

`cli/run.py` calls the decomposer once per session before the chunker
and writes an `instruction_decomposed` audit event. The chunker's
existing per-batch submission builder (added in project 1) is extended
to emit one `_Submission` per atomic rule. The Anthropic path is
entirely untouched.

## Motivation

The user's representative workload is a 56-page PACER PDF with a five-rule
`--instruct` (~800 chars):

> 1. All URLs (because some lead to CSAM and I don't trust the company to
>    remove all of them)…
> 2. The names of the complainants in the 'content' column, the 'fullname'
>    content (meaning the entire 'fullname' column should be redacted)…
> 3. The email addresses…
> 4. The IP addresses of the complainant in the 'content' column…
> 5. Please also redact the 'lastreplier' column…

On Anthropic Opus 4.7, this works fine: the model handles five rules in one
prompt and emits unioned findings. On Ollama with `llama3.1:8b`:

- The model returns malformed JSON (often missing closing brackets when
  the response is long), or
- The model selectively redacts only the first rule it processes, dropping
  the rest, or
- The model times out at the configured 120s read timeout.

The chunker's reactive subdivision (post-retry halving) doesn't help here:
the prompt fits the context window; the model just can't reason coherently
across N rules at once. Reducing scope **per call** is the fix.

## Goals

- On Ollama runs with `--instruct`, decompose multi-rule instructions and
  dispatch one provider call per rule.
- Keep the Anthropic path unchanged.
- Keep the chunker provider-agnostic (decomposition lives outside it).
- Decompose deterministically when possible; fall back to an LLM split
  only when the parser can't find structure.
- Best-effort fallback: if decomposition produces no useful split, run
  the original monolithic instruction (no regression vs. today).
- Surface decomposition decisions in the audit log so users can see what
  happened.

## Non-Goals

- Decomposing rule-set categories. Project 1 already routes per-category;
  this project addresses `--instruct` specifically.
- Anthropic-side decomposition. Anthropic Opus / Sonnet / Haiku handle
  multi-rule instructions reliably; decomposition adds latency without
  benefit.
- User-tunable decomposition threshold. The 300-char minimum for LLM
  fallback is baked in for v1; revisit if real workloads demand it.
- Rule-level finding provenance. Each per-rule call still produces
  findings tagged `source="instruction"`. The `instruction_decomposed`
  audit event captures which rules existed; per-finding rule attribution
  isn't required.
- Multi-shot LLM fallback retry. One attempt; on failure, use original.
- Markdown-flavored numbering variants (`1)`, `(1)`, etc.). Only `\d+\.`
  at line start in v1.

## Architecture

### Three concerns, three locations

**Decomposer** lives in a new `core/instruction_decomposer.py`. Pure
parser + minimally-coupled LLM fallback. Returns a typed
`DecompositionResult` and never raises.

**Orchestration** lives in `cli/run.py`. Gates decomposition on
`config.provider == "ollama" and instruct`. Calls the decomposer once
per session. Writes an audit event. Replaces `instruction_tuple` with
the decomposed rules.

**Dispatch** is the existing chunker. Two-line change in the submission
builder: where today there is one submission per batch carrying the
full instructions tuple, after this change there is one submission per
*element* of the tuple. The chunker doesn't know decomposition exists.

### Per-call shape

| Scenario | Today | After |
|---|---|---|
| Anthropic, single-rule `--instruct` | 1 call/batch | 1 call/batch (unchanged) |
| Anthropic, multi-rule `--instruct` | 1 call/batch | 1 call/batch (unchanged — decomposer doesn't run) |
| Ollama, single-rule `--instruct` | 1 call/batch | 1 call/batch (parser returns 1; threshold short-circuits LLM) |
| Ollama, multi-rule `--instruct` (5 rules) | 1 call/batch | 5 calls/batch |
| Ollama, long single-paragraph `--instruct` (>300 chars) | 1 call/batch | 1 LLM split call once + 1..N calls/batch |

## Components

### `src/kuroi/core/instruction_decomposer.py` (new)

Three module-level functions and one dataclass:

```python
@dataclass(frozen=True)
class DecompositionResult:
    rules: tuple[str, ...]
    source: Literal["original", "parser", "llm_fallback"]
    detail: str
```

- `rules`: the atomic sub-rules. Always at least one element. Equal to
  `(instruction,)` when no useful split was found.
- `source`: where the rules came from. `"original"` means the input was
  short or single-rule and never split; `"parser"` means the deterministic
  splitter found ≥2 rules; `"llm_fallback"` means the LLM split was
  attempted (it may still have failed and returned `(instruction,)`).
- `detail`: human-readable note for the audit log
  (e.g. `"parser found 5 rules via numbered prefix"`,
  `"llm fallback: timed out after 120s"`).

```python
def parse_instruction(instruction: str) -> tuple[str, ...]:
    """Pure function. Splits on numbering (`^\\d+\\.`), bullets (`^[-*]`),
    or blank-line-separated paragraphs, in that precedence. Returns ≥1
    trimmed non-empty rules. (instruction,) if no structure detected."""


def decompose(
    instruction: str,
    provider: Provider,
    *,
    threshold_chars: int = 300,
) -> DecompositionResult:
    """Parser first. If parser returns 1 rule AND len(instruction) >
    threshold_chars, dispatch one LLM split call against `provider`.
    Best-effort: any failure mode collapses to (instruction,) with
    source='llm_fallback'."""
```

Module-level constant:

```python
LLM_FALLBACK_THRESHOLD_CHARS = 300
"""Below this length, even a single-rule parse is left alone — short
instructions are unlikely to benefit from LLM splitting and aren't
worth the round-trip."""
```

The LLM fallback uses a parser-specific system prompt — *not* the
redaction system prompt — because the task is text splitting, not
document analysis:

```
You are a parser. Split the following redaction instruction into atomic
rules. Return JSON only: {"rules": ["rule 1", "rule 2", ...]}.
Each rule must be self-contained — a person reading just that rule
should know what to redact. Do not add rules that aren't in the input.

Instruction:
<instruction>
```

The fallback POSTs directly to `provider._url + "/api/chat"` with
`format: "json"`, reusing `provider._client`, with a fresh
`httpx.Timeout` matching `providers.ollama.READ_TIMEOUT_SECONDS` (120s
read/write/pool, 5s connect). The decomposer is intentionally
Ollama-specific (the gate at the call site ensures it only runs for
Ollama), so importing `READ_TIMEOUT_SECONDS` from `providers/ollama.py`
and accessing `provider._url` / `provider._client` is acceptable
coupling rather than abuse — the alternative would be a leaky public
surface on `OllamaProvider` for one consumer.

We don't reuse `provider.detect_redactions` because that signature
requires `pages` and uses the redaction system prompt — neither applies
to text splitting.

### `src/kuroi/cli/run.py`

After `instruction_tuple` is built and `provider` is constructed, before
`detect_redactions_chunked`:

```python
if config.provider == "ollama" and instruct:
    decomp = decompose(instruct, provider)
    instruction_tuple = decomp.rules
    audit.write_event(
        "instruction_decomposed",
        source=decomp.source,
        detail=decomp.detail,
        rule_count=len(decomp.rules),
        rules=list(decomp.rules),
    )
    if len(decomp.rules) > 1:
        console.print(
            f"  Decomposed instruction into {len(decomp.rules)} atomic rules "
            f"({decomp.source})"
        )
```

Imports add `from kuroi.core.instruction_decomposer import decompose`.

### `src/kuroi/core/chunking.py`

Two-line change in the submission-builder. Replace:

```python
        if instructions:
            submissions.append(
                _Submission(
                    model=provider.model,
                    category_ids=(),
                    instructions=instructions,
                )
            )
```

With:

```python
        for rule in instructions:
            submissions.append(
                _Submission(
                    model=provider.model,
                    category_ids=(),
                    instructions=(rule,),
                )
            )
```

When `instructions` is empty: zero submissions appended (today's behavior).
When length 1: one submission with the same single-rule shape (today's
behavior). When length N: N submissions. The Anthropic path passes a
length-1 tuple (no decomposition); the Ollama path passes a length-N
tuple after decomposition.

### `src/kuroi/core/audit.py` / `src/kuroi/core/audit_records.py`

The audit log gains a new event kind `instruction_decomposed`, written
once per session when decomposition fired (Ollama runs with
`--instruct`). Fields: `source`, `detail`, `rule_count`, `rules`.

The event is additive. No `ChunkRecord` change. Old audit JSONL readers
that filter by known event kind ignore the new line.

## Data flow

A single `kuroi run --provider ollama --instruct "<long multi-rule>"`
invocation:

**Step 1 — CLI loads and provider built.** Today's path. `instruct` and
`provider` are in scope.

**Step 2 — Decomposition gate.** `if config.provider == "ollama" and
instruct: decomp = decompose(instruct, provider)`. The decomposer:

1. **Parser pass** runs three strategies in precedence:
   a. Lines starting with `^\d+\.` — split on those.
   b. Lines starting with `^[-*]` — split on those.
   c. Two-or-more consecutive newlines — split on those.

   Each strategy is tried only if the previous didn't yield ≥2 non-empty
   rules. If none yields ≥2, parser returns `(instruction,)`.

2. **Threshold gate.** If parser returned exactly 1 rule AND
   `len(instruction) > LLM_FALLBACK_THRESHOLD_CHARS (300)`, the LLM
   fallback runs. Otherwise the parser's result is returned.

3. **LLM fallback (when triggered).** One `httpx.Client.post` to
   `<ollama_url>/api/chat` with system prompt for splitting,
   `format: "json"`, 120s read timeout. Parses
   `response.json()["message"]["content"]` as `{"rules": [...]}`. On any
   failure (timeout, HTTP error, JSON decode, missing key, fewer than 2
   rules, empty rules), returns `(instruction,)` with
   `source="llm_fallback"` and a `detail` describing the failure.

The decomposer never raises.

**Step 3 — Audit and console.** CLI writes `instruction_decomposed`
event with the source, detail, rule count, and rules. If `>1` rule,
prints a one-liner so the user sees what happened.

**Step 4 — Chunker dispatches.** `detect_redactions_chunked(...,
instructions=decomp.rules, ...)` runs as today, except submission-builder
emits one `_Submission` per rule. The existing `ThreadPoolExecutor`
(project 1) dispatches up to 4 concurrently per batch.

**Step 5 — Per-call request.** Each Ollama call carries a focused
prompt — same system prompt as today, but
`Redaction instructions: <single rule>` instead of the full instruction.
The model only has to handle one rule.

**Step 6 — Findings aggregation.** Each per-rule call returns its own
findings list. The chunker concatenates them into `aggregate_findings`.
The existing `_dedupe` step collapses identical `(page, start, end,
kind)` collisions, preserving the higher-confidence record on each. This
is the same code path project 1 used for category-group dispatch — no
new logic.

**Step 7 — Verification, output, audit close.** Unchanged.

### Worked example

User's PACER workload: 5 rules, 56 pages, `pages_per_batch=1`, Ollama on
`llama3.1:8b`, ~10s/call:

| | Today | After |
|---|---|---|
| Decomposition | n/a | parser hit (numbered); 0 LLM fallback calls |
| Calls per batch | 1 | 5 |
| Total Ollama calls | 56 | 280 (+ 0 for decomposition) |
| Wall-clock at concurrency=4 | ~10 min (often fails / drops rules) | ~12 min, all 5 rules consistently applied |

The slow path is acceptable because today's fast path is incorrect.

## Error handling and edge cases

The decomposer never raises. Every failure mode collapses to
`DecompositionResult(rules=(instruction,), source=..., detail=<reason>)`.
The CLI proceeds with the original monolithic instruction; the run
completes — same as today's behavior.

| Failure mode | Detection | `detail` |
|---|---|---|
| Ollama daemon unreachable | `httpx.ConnectError` | `"llm fallback: connection refused"` |
| Cold-start timeout | `httpx.TimeoutException` after 120s | `"llm fallback: timed out after 120s"` |
| HTTP 4xx/5xx | `httpx.HTTPStatusError` | `"llm fallback: HTTP <code>"` |
| Response not JSON | `json.JSONDecodeError` | `"llm fallback: response not valid JSON"` |
| Missing/wrong `rules` key | shape check after parse | `"llm fallback: response shape invalid"` |
| Returned <2 rules | `len(rules) < 2` after parse | `"llm fallback: only 1 rule returned"` |
| Empty/whitespace-only rules | filter step | dropped from result; if all empty, treated as <2 case |

### Parser edge cases

- **Mixed numbering and prose** (`"1. First rule. Some explanation. 2.
  Second rule."`): numbering wins; each `^\d+\.` line starts a new rule
  carrying its following prose.
- **Inline numbers in single rule** (`"Redact phone numbers like
  555-1234 and 555-5678."`): `^\d+\.` is anchored to line start, so
  inline numbers don't split. Tested explicitly.
- **Markdown-flavored numbering** (`1)` instead of `1.`): not supported
  in v1. Falls through to the other strategies.
- **Trailing/leading whitespace on rules**: stripped.
- **Empty/whitespace-only rules**: filtered out.
- **Single-paragraph prose**: parser returns `(instruction,)`. If the
  input exceeds 300 chars, the LLM fallback runs.

### Empty instructions

`--instruct ""` (or omitted): `instruct` is empty. CLI's
`if config.provider == "ollama" and instruct:` short-circuits. No
decomposer call, no audit event. Chunker emits zero instruction
submissions per batch — exact today behavior.

### Anthropic path

`if config.provider == "ollama"` is the *only* enabling check. Anthropic
runs never call the decomposer; their submission shape is a single-rule
`instructions=(instruct,)` tuple, identical to today.

### Threshold boundary

Single-rule instruction of length exactly 300 — fallback does NOT run
(uses `>` not `>=`).

### Concurrency

Decomposer runs once before the chunker, on the main thread. The Ollama
provider's `_client` (httpx) is thread-safe per its documentation; no
new concurrency concerns. The decomposer does not interact with the
chunker's `ThreadPoolExecutor`.

### Findings provenance

Each finding from a per-rule call still carries `source="instruction"`,
matching today's tagging. The audit log's `instruction_decomposed`
event captures which rules existed; per-finding rule attribution isn't
required and isn't added.

### Audit forward compat

Old audit JSONL readers must already tolerate unknown event kinds
(events are filtered by an `event` key, not parsed exhaustively).
`instruction_decomposed` is additive; no migration.

## Testing

| Layer | Test | What it asserts |
|---|---|---|
| Parser | `test_parser_splits_numbered_list` | `"1. foo\n2. bar"` → `("1. foo", "2. bar")` |
| Parser | `test_parser_splits_bulleted_list` | `"- foo\n- bar"` → `("- foo", "- bar")` |
| Parser | `test_parser_splits_blank_line_paragraphs` | two paragraphs → 2 rules |
| Parser | `test_parser_returns_single_rule_for_prose` | prose without structure → 1 rule |
| Parser | `test_parser_ignores_inline_numbers` | `"Redact ZIPs like 12345 and 67890."` → 1 rule |
| Parser | `test_parser_strips_whitespace_around_rules` | `"  1. foo\n  2. bar  "` → trimmed |
| Parser | `test_parser_drops_empty_rules` | `"1. foo\n2. \n3. bar"` → 2 rules |
| Parser | `test_parser_numbering_takes_precedence_over_bullets` | input has both → numbered split wins |
| Decomposer | `test_decompose_short_input_skips_llm_fallback` | 100-char single-rule input → no `provider._client.post` call |
| Decomposer | `test_decompose_uses_parser_result_when_multi_rule` | parser hits → no LLM fallback dispatched |
| Decomposer | `test_decompose_llm_fallback_on_long_unstructured` | parser returns 1, len>300 → fallback runs, returns `source="llm_fallback"` |
| Decomposer | `test_decompose_falls_back_on_timeout` | client raises `TimeoutException` → original returned, detail mentions timeout |
| Decomposer | `test_decompose_falls_back_on_malformed_json` | client returns non-JSON → original returned |
| Decomposer | `test_decompose_falls_back_on_single_rule_response` | LLM returns `{"rules": ["X"]}` → original returned |
| Decomposer | `test_decompose_falls_back_on_missing_key` | LLM returns `{"foo": "bar"}` → original returned |
| Decomposer | `test_decompose_uses_120s_read_timeout` | mock client; assert `httpx.Timeout` argument matches `READ_TIMEOUT_SECONDS` |
| Decomposer | `test_decompose_threshold_boundary_at_300` | input length exactly 300 → no fallback (uses `>`) |
| Chunker | `test_per_rule_submissions_emitted` | passing `instructions=("a","b","c")` → 3 submissions per batch |
| Chunker | `test_single_rule_instructions_keeps_one_submission` | `instructions=("only one",)` → 1 submission per batch (today's shape preserved) |
| CLI | `test_run_ollama_decomposes_before_dispatch` | end-to-end stub: 5-numbered-rule `--instruct` on Ollama → 5 submissions per batch + `instruction_decomposed` audit event |
| CLI | `test_run_anthropic_does_not_decompose` | end-to-end stub: same instruction on Anthropic → 1 submission per batch + no decomposition audit event |
| CLI | `test_run_ollama_no_instruct_does_not_decompose` | Ollama run without `--instruct` → no decomposer call, no audit event |
| Audit | `test_instruction_decomposed_event_serializes` | event with all fields lands in JSONL |

No real Ollama daemon is started in tests — all decomposer LLM-fallback
paths use a mock httpx client (matches the existing Ollama test pattern).

## Rollout

The change is additive and backward-compatible:

- Anthropic users see no change.
- Ollama users with single-rule `--instruct` see no change (parser returns
  1 rule, threshold short-circuits the fallback, chunker emits 1
  submission).
- Ollama users with multi-rule `--instruct` see decomposition, an
  informational console line, and an audit event. Their findings are now
  reliable where today they were partial. Wall-clock is roughly equal
  to today's failed-run wall-clock.
- The CLI surface is unchanged; no new flags.
- Audit JSONL is forward-compatible: new event kind, additive.

No migration required.

## Estimated diff

~250 lines source + ~200 lines tests across 5 files, in 6–8 small
commits.

## Open follow-ups

- **User-tunable threshold.** If 300 chars proves wrong for real
  workloads, expose `KUROI_DECOMPOSE_THRESHOLD` env var or
  `--decompose-threshold` flag.
- **Markdown-flavored numbering** (`1)`, `(1)`, `i.`). Add patterns to
  the parser if users hit them.
- **Per-rule finding provenance.** If audit consumers want to know which
  rule produced which finding, add a per-rule index to `ChunkRecord` or
  `Finding` and surface it in the audit event.
- **Multi-shot LLM fallback retry.** If the single-shot fallback fails
  often on real workloads, try a second prompt phrasing before giving up.
- **Anthropic-side decomposition.** If users want fewer total tokens
  even on the Anthropic path (each rule gets its own focused prompt =
  shorter), revisit. Today's Anthropic prompt-caching wins make this
  less attractive.
