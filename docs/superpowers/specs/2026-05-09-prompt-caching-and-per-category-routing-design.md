# Prompt Caching and Per-Category Model Routing

**Date:** 2026-05-09
**Status:** Draft

## Overview

Two cost-reducing changes to the Anthropic LLM path, shipped together because
they share the same provider and chunker surface:

1. **Prompt caching.** The system prompt and the static prefix of the user
   prompt (categories list + instructions + schema hint) are marked with
   `cache_control: {"type": "ephemeral"}`. The variable `<document>` block
   is uncached. After the first batch of a session, subsequent batches read
   the cached prefix at ~10% of the input rate.
2. **Opt-in per-category model routing.** `Category` gains an optional
   `model: str | None` field. When set, the chunker dispatches that
   category's calls against the named model (e.g. `email_llm → claude-haiku-4-5`)
   instead of the run's global model. Categories with `model = None` keep
   the global model; existing rule packs are unchanged.

Per-batch model groups are dispatched concurrently inside the chunker, so
opting in to mixed-model routing doesn't multiply wall-clock latency.

Free-form `--instruct` instruction routing is **not** in scope here. It
stays on the run's global model and benefits from caching only. Project 2
(auto-decompose big instructions for small models) will revisit it.

## Motivation

The user's representative workload is a 56-page PACER PDF processed in
56 single-page batches with a long free-form `--instruct` (~800 chars,
five numbered rules). Today every batch resends the full system prompt +
800-char instruction + schema as fresh input tokens — ~1180 redundant
tokens × 56 batches. At Opus 4.7 prices ($15/MTok input, $75/MTok output)
that's ~$0.99 of pure prefix overhead per run.

For users running rule sets, a different problem: every active LLM
category goes to the same model. Easy categories (e.g. an LLM-only
fallback for emails not caught by regex) cost the same as hard ones
(e.g. person names, signatures). There's no way to route by difficulty.

Caching addresses the first; per-category routing addresses the second.
Both are opt-in or transparent; neither breaks existing setups.

## Goals

- Cut redundant input tokens on the static prompt prefix for every
  Anthropic run (instructions, rule sets, or both).
- Let users assign different LLM categories to different models.
- Preserve existing default behavior when no category declares a `model`.
- Keep wall-clock latency comparable when users opt in to mixed-model
  routing.
- Keep the audit log additive — old JSONL files keep parsing.

## Non-Goals

- Routing free-form `--instruct` to per-rule models (project 2).
- Anthropic Message Batches API (50% off, 24h SLA) — out of scope per
  user preference.
- Compact word-index prompt encoding — out of scope per user preference.
- Caching for Ollama — Ollama has no cache concept; provider is updated
  for protocol parity only (accepts `model` override; ignores caching).
- Shipping default per-category model assignments in `pii-en.yaml` —
  out of scope; users opt in explicitly.
- 1-hour cache TTL. Ephemeral (5-minute) is sufficient: a typical run
  completes within minutes; the longer TTL costs more on writes and
  doesn't help once the run ends.

## Architecture

The change touches three surfaces:

**Provider — caching layer.** `AnthropicProvider.detect_redactions`
switches from string `system=` and string user-content to the structured
typed-block form. Two cache breakpoints land on (a) the system prompt
block and (b) the static prefix of the user content. The `<document>`
block stays uncached. Two of Anthropic's four cache-breakpoint slots are
used; two remain available for future use.

**Chunker — per-model dispatch.** Active LLM categories are partitioned
by their target model once per session. For each page-batch, the chunker
dispatches one provider call per non-empty model group. Groups for the
same batch run concurrently in a bounded `ThreadPoolExecutor`. Free-form
`instructions` get their own dispatched call on the run's global model
(deferred to project 2). Subdivision and retry behavior are unchanged
inside `_try_or_subdivide`; the model parameter is threaded through.

**Audit & pricing — accurate cost reporting.** `ChunkRecord` gains
`cache_creation_input_tokens` and `cache_read_input_tokens`, defaulting
to 0. `pricing.rates` gains `cache_write_multiplier = 1.25` and
`cache_read_multiplier = 0.1`. Final cost computation in `cli/run.py`
factors all four token classes.

### Per-batch call shape

| Scenario | Today | After |
|---|---|---|
| `--instruct` only | 1 call | 1 call (cached) |
| Rule-set categories, all default model | 1 call | 1 call (cached) |
| Rule-set categories, mixed models | 1 call | N calls (one per model group, concurrent) |
| Both `--instruct` and rule-set categories | 1 call | 1 + N calls (concurrent) |

Caching is universal (always on for Anthropic). Per-call multiplication
only happens when a user assigns multiple distinct `model` values across
their active categories.

## Components

### `src/kuroi/providers/_shared.py`

- New `build_system_blocks() -> list[dict]` returns the system prompt as
  a typed-block list with a `cache_control: {"type": "ephemeral"}`
  marker. Used by the Anthropic path.
- Split `build_user_prompt` into two helpers:
  - `build_user_static_prefix(category_ids, instructions) -> str`
    (cacheable: categories list + instructions + schema hint).
  - `build_user_document_block(pages, layout_aware) -> str`
    (variable: `<document>...</document>`).
- Existing `build_user_prompt` becomes a thin combinator returning the
  joined string for the Ollama path.
- `parse_findings_payload` is unchanged.

### `src/kuroi/providers/anthropic.py`

- `detect_redactions` accepts a new optional keyword `model: str | None`.
  When `None`, falls back to the instance's configured `self.model`.
- Switches to the structured-blocks request form: `system=[{type, text,
  cache_control}]` and `messages=[{role:"user", content:[{type:"text",
  text:static, cache_control}, {type:"text", text:doc}]}]`.
- Reads `usage.cache_creation_input_tokens` and
  `usage.cache_read_input_tokens` from the response and threads them
  into the returned `ChunkRecord`.
- Adds a typed model-not-found classifier alongside `_is_prompt_too_long`.
  When raised, the provider re-raises a typed `ConfigError` rather than
  returning `[], []` (which would trigger pointless subdivision).

### `src/kuroi/providers/ollama.py`

- `detect_redactions` accepts `model: str | None` for protocol parity.
  When provided and different from `self.model`, uses the override; else
  uses `self.model`. No caching changes — Ollama doesn't support cache.

### `src/kuroi/providers/base.py`

- `Provider.detect_redactions` Protocol gains `model: str | None = None`
  keyword.

### `src/kuroi/core/rules.py`

- `Category` gains `model: str | None = None`.
- YAML loader reads optional `model:` field; absence yields `None`.

### `src/kuroi/core/chunking.py`

- New helper:
  `_partition_categories_by_model(categories, default_model) -> dict[str, tuple[str, ...]]`.
  Returns `{model: (cat_id, ...), ...}` for the active LLM categories.
  Computed once per session.
- `detect_redactions_chunked` loops over `model_groups.items()` for each
  page-batch and submits each group's `_try_or_subdivide` call to a
  bounded `ThreadPoolExecutor`, sized to `min(len(submissions),
  MAX_CONCURRENT_GROUPS=4)`. When `instructions` are present, an
  additional submission is made for the global model with empty
  `category_ids`. Results are collected by calling `.result()` on the
  futures **in submission order** to keep audit-log ordering
  deterministic, regardless of which group's API call returns first.
- `_try_or_subdivide` and `_try_with_retries` accept `model: str` and
  pass it to the provider. Subdivision children inherit the parent's
  model.
- `on_batch_complete` callback signature changes from `chunk: ChunkRecord`
  to `summary: BatchSummary`, where `BatchSummary` is a small
  dataclass aggregating per-batch metrics across groups: `duration_ms =
  max(group durations)` (concurrent), `tokens_in/tokens_out = sum`,
  plus the underlying tuple of `ChunkRecord` for callers that want
  per-group detail. This preserves the one-line-per-batch UX in
  `cli/run.py`. Individual `ChunkRecord`s are still written to the
  audit log unchanged.
- Aggregation otherwise unchanged: `_dedupe` and `_translate_indices`
  already handle multiple `ChunkRecord`s per batch.

### `src/kuroi/core/audit_records.py`

- `ChunkRecord` gains:
  - `cache_creation_input_tokens: int = 0`
  - `cache_read_input_tokens: int = 0`
- Defaults preserve compatibility for old JSONL files.

### `src/kuroi/core/pricing.py`

- `Rates` gains `cache_write_multiplier: float = 1.25` and
  `cache_read_multiplier: float = 0.1`.
- `estimate_cost` continues to use the regular input rate (pre-call
  estimation is cache-unaware).
- Actual cost computation in `cli/run.py` uses:
  `(tokens_in * input_per_million
    + cache_write_tokens * input_per_million * cache_write_multiplier
    + cache_read_tokens  * input_per_million * cache_read_multiplier
    + tokens_out * output_per_million) / 1_000_000`
  Anthropic's `usage.input_tokens` already excludes cached tokens, so
  no subtraction is needed — the four counters partition total billed
  input tokens cleanly.

### `src/kuroi/core/audit.py`

- `write_event("chunk_request", ...)` and `ChunkRecord` serialization
  include the new cache fields. JSONL forward compatibility is enforced
  by defaults: old logs that lack these fields parse to `0`.

## Data flow

A single page-batch through the system after the change:

**Step 1 — partition once per session.** Before the batch loop,
`detect_redactions_chunked` builds `model_groups` from the active
categories and the run's global model. Identical model strings collapse
to one group. The result is reused across all batches.

**Step 2 — per-batch concurrent dispatch.** For each batch, submit one
`_try_or_subdivide` task per non-empty model group, plus one task for
`instructions` against the global model when instructions are non-empty,
to a `ThreadPoolExecutor` sized to the submission count (capped at 4).
Each task runs the existing retry/subdivide loop end to end. Results
are collected by calling `future.result()` in submission order so that
audit log entries land in a deterministic order regardless of which
group's API call returns first.

**Step 3 — provider builds the request.** `AnthropicProvider`
constructs the structured-blocks request: system prompt with cache
marker + `[static prefix block with cache marker, document block
without]`. The first batch of the session triggers cache writes; all
subsequent batches within 5 minutes read the cached prefix.

**Step 4 — record cache tokens.** The provider reads
`cache_creation_input_tokens` and `cache_read_input_tokens` from
`usage` and threads them into the returned `ChunkRecord`. They land in
the JSONL and the in-memory aggregate.

**Step 5 — cost computed at run end.** `cli/run.py` sums the four token
classes across all chunks and applies the cache multipliers when
computing actual cost.

## Worked example

User's 56-batch PACER run, instruction-only, Opus 4.7
($15/MTok input, $75/MTok output):

- Static prefix: ~1180 tokens / request (system + 800-char instruction + schema)
- Document block: ~4200 tokens / request
- Output: ~400 tokens / request

| | Today | After |
|---|---|---|
| Cache write (1 × 1180 × 1.25 × $15/M) | — | $0.022 |
| Cache read (55 × 1180 × 0.1 × $15/M) | — | $0.097 |
| Regular input (uncached today; document only after) | 56 × 5380 × $15/M = $4.52 | 56 × 4200 × $15/M = $3.53 |
| Output (56 × 400 × $75/M) | $1.68 | $1.68 |
| **Total** | **$6.20** | **$5.33** |

That's ~14% off this representative workload from caching alone.
Larger savings appear once project 2 lets us route decomposed
instructions to Sonnet/Haiku.

## Error handling and edge cases

**Cache miss / silent skip.** Anthropic skips caching when a cache
block exceeds size limits and reports `cache_creation_input_tokens=0,
cache_read_input_tokens=0`. A debug-level log line on batch 2+ when
both counters are zero makes silent misses diagnosable; we don't fail.

**Cache TTL expiry.** Invisible to us — we just see fewer reads in
`usage`. No special handling.

**Unknown / unavailable `Category.model`.** Anthropic raises
`BadRequestError("model not found")`. The provider classifies this as a
hard config error (alongside the prompt-too-long classifier) and raises
a typed `ConfigError` so the chunker doesn't subdivide pointlessly.
`cli/run.py` surfaces a user-friendly message naming the offending
category and exits with code 2.

**Empty model group / partition collapse.** When all active categories
default to the global model, partition produces a single group
identical to today's behavior. Tested explicitly.

**Mixed instructions + categories.** Per data flow, instructions get
their own call on the global model. When only instructions are set and
no categories, exactly one call is made per batch — same shape as
today, just with caching applied.

**Subdivision interaction.** Subdivision children inherit the parent's
model. The chunker doesn't switch models mid-subdivide. Findings of the
same `kind` from different model-group calls cannot collide because each
group owns disjoint categories.

**Old audit logs.** `cache_*` fields default to 0 in `ChunkRecord`;
existing readers (`cli/diff.py`, `cli/backups.py`, audit replay) treat
absent fields as 0. No migration needed.

**Old rule packs.** `Category.model = None` by default. Existing
`pii-en.yaml` works unchanged.

**Concurrency safety.** `AnthropicProvider` is stateless across calls
(only `model`, `max_tokens`, and `_client` on the instance; SDK calls
are independent). The `ThreadPoolExecutor` shares one provider instance
across threads — safe by inspection. Per-batch concurrency is bounded
to the group count (typically 2-3, never more than the number of
distinct models in the rule set).

## Testing

| Layer | Test | What it asserts |
|---|---|---|
| Provider | `test_anthropic_includes_cache_control` | system + static-prefix blocks have `cache_control: {"type": "ephemeral"}`; document block does not |
| Provider | `test_anthropic_records_cache_tokens` | response with `cache_read_input_tokens=120` lands as `chunk.cache_read_input_tokens=120` |
| Provider | `test_anthropic_uses_per_call_model` | `model="claude-haiku-4-5"` keyword overrides the instance's configured model |
| Provider | `test_anthropic_unknown_model_is_hard_error` | model-not-found raises `ConfigError`, doesn't return `[], []` |
| Rules | `test_category_model_field_optional` | YAML without `model:` parses; YAML with `model:` populates the field |
| Chunking | `test_partition_by_model_groups_categories` | mixed-model categories produce correct `{model: cat_ids}` map |
| Chunking | `test_uniform_model_dispatches_one_call_per_batch` | all-default categories preserve today's call count |
| Chunking | `test_mixed_models_dispatch_per_group` | two model groups → 2 calls per batch; findings union correctly |
| Chunking | `test_instructions_route_to_global_model` | instructions get their own call on global model when categories are mixed |
| Chunking | `test_groups_dispatched_concurrently` | clock-time of N-group batch ≈ max(group time), not sum (uses synthetic provider with controlled sleep) |
| Pricing | `test_cost_factors_cache_write_and_read` | `1.25 × write + 0.1 × read + regular` matches expected $ |
| Audit | `test_chunk_record_serialization_includes_cache_fields` | new fields present in JSONL |
| Audit | `test_old_chunk_record_loads_with_zero_cache_fields` | backward-compat for old logs |
| Integration | `test_run_with_routing_produces_expected_findings` | end-to-end on a synthetic PDF with two LLM categories on different models, verify findings union correctly (stubbed provider via `client=` injection) |

No test makes a real Anthropic API call. All cache-token / model-routing
behavior is tested via the existing `client=` mock injection seam.

Existing tests that need updates (signature-only):

- `tests/core/test_chunking.py::test_on_batch_complete_receives_renumbered_chunk`
- `tests/core/test_chunking.py::test_on_batch_complete_does_not_fire_on_hard_failure`
- `tests/core/test_chunking_subdivision.py` (callbacks-fire-once tests)
- `tests/cli/test_run.py` (callback wire-up assertions)

Each migrates from inspecting a single `ChunkRecord` argument to the
new `BatchSummary` dataclass; semantic assertions are unchanged.

## Rollout

The change is additive and backward-compatible:

- Existing rule packs work without edits (`Category.model = None`).
- Existing audit JSONL files continue to parse (`cache_*` fields default
  to 0).
- The CLI surface is unchanged; no new flags.
- The default behavior on a fresh install with `pii-en` and no
  `--instruct` is "today + caching" — strictly faster and cheaper.

No migration step required.

## Estimated diff

~400 lines across 9 source files, plus ~200 lines of test, in 8-10
small commits.

## Open follow-ups

- **Project 2 (auto-decompose big instructions).** Will parse
  `--instruct` into N atomic rules, each becoming a synthetic category.
  Once shipped, those synthetic categories can carry a `model` field and
  reuse this project's routing plumbing.
- **Default model assignments in `pii-en.yaml`.** Once we have field
  data on Sonnet/Haiku recall vs Opus on `person_name` and
  `street_address`, ship sensible defaults.
- **1-hour cache TTL.** Reconsider for users who run kuroi in tight
  loops on the same prompt skeleton (e.g. CI pipelines processing many
  similar docs back-to-back).
