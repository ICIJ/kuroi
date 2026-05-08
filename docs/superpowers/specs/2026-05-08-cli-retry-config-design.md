# Configurable Retry Policy for LLM Calls

**Date:** 2026-05-08
**Status:** Draft

## Overview

Expose retry behavior — attempt count, base backoff, growth multiplier — as
end-user configuration. Today these are baked into a module-level constant
in `src/kuroi/core/chunking.py` and only apply when `--pages-per-batch >= 1`.
This spec makes them configurable via CLI flags, environment variables, and
the TOML config file (matching the existing `provider`/`model`/`ollama_url`
pattern), and unifies the default and chunked code paths so retry applies
on every run by default.

Defaults reproduce today's behavior on the chunked path. The default
(non-chunked) path gains retry as a strict improvement.

## Motivation

The retry layer added in `2026-05-07-pages-per-batch-design.md` covers two
real failure modes (Ollama timeouts, transient HTTP errors), but its policy
is hardcoded:

```python
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 4.0)
MAX_ATTEMPTS = len(RETRY_BACKOFFS_SECONDS) + 1
```

Four scenarios drive this change:

1. **Crank retries up.** Slow CPU + large Ollama model → 2 retries with 6s
   total backoff isn't enough; users want 5+ retries with longer waits.
2. **Fail fast.** In CI or a debug loop, the user wants 0 retries so a
   broken provider config surfaces immediately instead of waiting 6+ seconds.
3. **Tune backoff delays.** A 2s/4s schedule is the wrong shape for some
   providers (rate-limit cooloffs want 30s+; flaky network wants 0.5s).
4. **Apply retries to the default path.** Users on `--pages-per-batch 0`
   (the default) currently see no retry — a single hard failure produces
   "No redactions proposed" with the WARN logs as the only signal.

## Public surface

### CLI flags on `kuroi run`

```
--max-retries N                 Default 2. 0 disables retry; max attempts = N + 1.
--retry-backoff SECONDS         Default 2.0. Seconds before retry #1.
--retry-backoff-multiplier M    Default 2.0. Each subsequent retry waits M× the previous.
```

### Environment variables

- `KUROI_MAX_RETRIES`
- `KUROI_RETRY_BACKOFF`
- `KUROI_RETRY_BACKOFF_MULTIPLIER`

### TOML — new `[retry]` table

```toml
[retry]
max_retries = 2
backoff = 2.0
backoff_multiplier = 2.0
```

Resolution precedence is unchanged from the rest of `Config`: CLI flag >
env var > TOML > built-in default, resolved per-key independently. A user
can pin `max_retries` in TOML and override only the multiplier on the CLI
for a single run.

### Schedule formula

Delay before retry *k* (1-indexed) is:

```
backoff × backoff_multiplier^(k-1)
```

The defaults `(2, 2.0, 2.0)` produce the schedule `(2.0, 4.0)` — exactly
today's `RETRY_BACKOFFS_SECONDS`. Examples:

| Flags | Schedule (seconds) |
| --- | --- |
| `--max-retries 0` | `()` — fail fast |
| (defaults) | `(2.0, 4.0)` |
| `--max-retries 5 --retry-backoff 1 --retry-backoff-multiplier 2` | `(1, 2, 4, 8, 16)` |
| `--max-retries 4 --retry-backoff 3 --retry-backoff-multiplier 1` | `(3, 3, 3, 3)` — fixed delay |

### Validation

Each invalid value raises `ConfigError` (caught in `cli/run.py` and exits
with code 2, matching existing config errors):

- `max_retries`: integer, ≥ 0. Negative or non-int → error.
- `backoff`: int or float, ≥ 0. `0` is valid (retry with no sleep).
- `backoff_multiplier`: int or float, ≥ 1.0. Sub-1 multipliers shrink
  delays — disallowed as a footgun. Use `1.0` for fixed delay.

## Architecture

### `src/kuroi/core/config.py` — new `RetryPolicy` value object

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    backoff: float
    backoff_multiplier: float

    def schedule(self) -> tuple[float, ...]:
        return tuple(
            self.backoff * (self.backoff_multiplier ** k)
            for k in range(self.max_retries)
        )

DEFAULT_RETRY_POLICY = RetryPolicy(
    max_retries=2, backoff=2.0, backoff_multiplier=2.0
)
```

Changes to existing types in the same file:

- `Config` gains `retry: RetryPolicy = DEFAULT_RETRY_POLICY`.
- `ConfigOverrides` gains three optional fields:
  `retry_max: int | None`, `retry_backoff: float | None`,
  `retry_backoff_multiplier: float | None`.
- `resolve_config` reads the `[retry]` TOML table and the three
  `KUROI_RETRY_*` env vars per key, then constructs the final
  `RetryPolicy`. Each key walks CLI → env → file → built-in default
  independently.
- `write_config_file` is **not** updated. The on-disk writer stays
  scoped to what `kuroi setup` writes (provider/model/ollama). Users
  who want retry pinned in TOML edit the file directly — matching
  how `audit.include_text` and `backup.retention_hours` already
  behave.

### `src/kuroi/core/chunking.py` — drop module constants, accept policy

Module-level `RETRY_BACKOFFS_SECONDS` and `MAX_ATTEMPTS` are deleted.
`detect_redactions_chunked` gains a required keyword argument:

```python
def detect_redactions_chunked(
    provider: Provider,
    pages: tuple[Page, ...],
    llm_category_ids: tuple[str, ...],
    *,
    instructions: tuple[str, ...] = (),
    seed: int | None = None,
    pages_per_batch: int,
    retry_policy: RetryPolicy,                           # new, required
    on_batch_start: Callable[...] | None = None,
    on_batch_complete: Callable[...] | None = None,
) -> tuple[list[Finding], list[ChunkRecord]]:
```

The retry loop computes `schedule = retry_policy.schedule()` once per
batch; total attempts per batch = `len(schedule) + 1`. `BatchError.attempts`
is derived from the policy rather than a global constant. The existing
`logger.info("retrying batch %d/%d (pages %s) in %.0fs (attempt %d/%d)", ...)`
log line is unchanged in shape.

### `src/kuroi/cli/run.py` — collapse the dual code path

The `if pages_per_batch == 0: ... else: ...` branch at the current
`cli/run.py:203-246` disappears. The unified call:

```python
effective_batch_size = pages_per_batch if pages_per_batch > 0 else len(pages)
total_batches = math.ceil(len(pages) / effective_batch_size)
batched_ui = total_batches > 1   # only when user opted into chunking

provider_findings, chunks = detect_redactions_chunked(
    provider,
    pages,
    tuple(llm_cat_ids),
    instructions=instruction_tuple,
    seed=seed,
    pages_per_batch=effective_batch_size,
    retry_policy=config.retry,
    on_batch_start=_on_batch_start if batched_ui else None,
    on_batch_complete=_on_batch_complete if batched_ui else None,
)
```

The progress callbacks (`_on_batch_start`, `_on_batch_complete`) only fire
when the user opted into chunking (`total_batches > 1`), so default-path
output is byte-identical to today's. `BatchError` handling moves out of the
former `else` branch and wraps the unified call.

Three new Typer options are added to `run()` — `--max-retries: int | None`,
`--retry-backoff: float | None`, `--retry-backoff-multiplier: float | None`
— and passed into `ConfigOverrides`. The `--pages-per-batch` flag stays
exactly as-is.

### `src/kuroi/providers/factory.py` and `src/kuroi/providers/*.py`

No changes. Providers stay single-call; the orchestrator owns retry. The
existing comment in `providers/anthropic.py:73` noting that the Anthropic
SDK has its own retry/timeout layer remains accurate — that layer is
independent of the orchestrator-level policy.

## Behavior change for default-path users

Users on `--pages-per-batch 0` (the default) previously got **zero retries**
on hard provider failures and saw "No redactions proposed" with WARN logs as
the only signal. After this change they get the default policy (2 retries,
2s/4s backoff) — a strict improvement aligned with the original chunking
spec's intent. Documented in `CHANGELOG.md` under the next release. The
fail-fast escape hatch is `--max-retries 0`.

## Failure handling

The hard/soft distinction from `2026-05-07-pages-per-batch-design.md` is
unchanged:

- **Hard failure** (HTTP error, timeout, connection refused, malformed
  envelope JSON): provider returns `chunks == []`. Orchestrator sleeps for
  the next entry in `policy.schedule()` and retries. Exhausting the
  schedule raises `BatchError`.
- **Soft outcome** (response was JSON, model returned `{"findings": []}`):
  provider returns `chunks == [chunk]` with `findings == []`. No retry —
  same reasoning as before (temperature=0 means a retry produces the same
  answer; truncation/non-JSON WARNINGs already fired).

`BatchError.attempts` continues to be the total attempt count
(`policy.max_retries + 1`) and remains usable in the existing user-facing
message (`"Batch 4 (pages 16–20) failed 3 times and was aborted."`).

## Audit

No schema change. `ChunkRecord` is unchanged; the audit log gains no new
fields. The retry policy itself is **not** persisted in the audit because
it's user-level operational tuning and recoverable from rerunning with the
same flags. (Consider revisiting if reproducibility audits later want it.)

## Tests

### `tests/core/test_config.py` (file already exists; new tests added)

1. `test_retry_policy_schedule_default` —
   `DEFAULT_RETRY_POLICY.schedule() == (2.0, 4.0)`.
2. `test_retry_policy_schedule_zero_retries` —
   `RetryPolicy(0, 2.0, 2.0).schedule() == ()`.
3. `test_retry_policy_schedule_fixed_delay` —
   `RetryPolicy(3, 5.0, 1.0).schedule() == (5.0, 5.0, 5.0)`.
4. `test_resolve_config_retry_defaults_when_unset` — empty file, no env, no
   overrides → `config.retry == DEFAULT_RETRY_POLICY`.
5. `test_resolve_config_retry_from_toml` — `[retry]` table is honored.
6. `test_resolve_config_retry_from_env_overrides_file` — env beats TOML.
7. `test_resolve_config_retry_cli_overrides_env` — CLI overrides win.
8. `test_resolve_config_retry_per_key_independent` — TOML sets
   `max_retries`; CLI overrides only `backoff_multiplier`; result merges.
9. `test_resolve_config_rejects_negative_max_retries`.
10. `test_resolve_config_rejects_non_int_max_retries` (e.g., float `2.5`).
11. `test_resolve_config_rejects_negative_backoff`.
12. `test_resolve_config_rejects_multiplier_below_one` (e.g., `0.5`).
13. `test_resolve_config_rejects_non_table_retry` (e.g.,
    `retry = "fast"` at top level).

### `tests/core/test_chunking.py` (existing file; tests updated)

- All existing tests are updated to pass `retry_policy=DEFAULT_RETRY_POLICY`
  (or a test-local policy) as a keyword argument.
- The two tests that previously asserted on
  `chunking_mod.RETRY_BACKOFFS_SECONDS` switch to constructing a policy
  directly and asserting on its `.schedule()`.
- New: `test_chunked_call_with_zero_retries_aborts_on_first_hard_failure`
  — `RetryPolicy(0, ...)` + one hard-failing scripted call → `BatchError`,
  `len(provider.calls) == 1`, no `time.sleep` invoked.
- New: `test_chunked_call_uses_policy_schedule_for_sleeps` —
  `RetryPolicy(3, 1.0, 3.0)` → asserts `sleeps == [1.0, 3.0, 9.0]`.
- New: `test_chunked_call_attempts_in_batch_error_match_policy` —
  `BatchError.attempts == policy.max_retries + 1`.

### `tests/cli/test_run.py`

- New: `test_run_default_path_now_uses_orchestrator_with_default_policy`
  — stub provider; assert one batch, retry policy on the chunked call
  equals `DEFAULT_RETRY_POLICY`, no batch-progress lines printed.
- New: `test_run_max_retries_zero_disables_retry` — flag end-to-end smoke
  test using a stub provider that hard-fails once.

Existing tests for the default path continue to pass because output is
byte-identical when `total_batches == 1`.

## Documentation

- `docs/reference/cli.md` — auto-regenerated by `make docs-gen` from the
  Typer app. No manual edit.
- `docs/reference/config.md` — three new rows in the keys table:
  `max_retries`, `retry_backoff`, `retry_backoff_multiplier`. The example
  TOML gains a `[retry]` block. Note that defaults reproduce the historical
  schedule.
- `docs/user-guide/troubleshooting.md` — one short paragraph:
  > Hitting transient Ollama timeouts or rate-limit errors? Raise
  > `--max-retries` (or pin `[retry] max_retries = 5` in your config). For
  > fail-fast in CI, pass `--max-retries 0`.
- `CHANGELOG.md` — entry under next release:
  > **feat(retry):** expose configurable retry policy via CLI / `KUROI_RETRY_*`
  > env vars / `[retry]` TOML table. The default (non-chunked) path now
  > retries on hard provider failures (was: no retry).

## Out of scope

- **Per-provider policies.** One global policy applies regardless of the
  active provider. Users who want different settings for Anthropic vs
  Ollama swap TOML when they swap provider.
- **Jitter.** No randomization in the schedule. Could be added later as a
  `backoff_jitter` knob.
- **Max-backoff cap.** No upper bound — wild multipliers are the user's
  problem. Could add `backoff_max` later if it comes up.
- **Retry on specific HTTP status codes.** Provider-internal: providers
  continue to convert all hard failures (HTTP errors, timeouts, malformed
  JSON) to `chunks == []`. The orchestrator can't distinguish causes.
- **Coordinating with the Anthropic SDK's own internal retries.** That
  layer is independent and separately tunable on the SDK client; out of
  scope here.
- **Updating `kuroi setup`** to prompt for retry. Setup stays minimal;
  retry is configured by flag or direct TOML edit.
- **Persisting retry policy in the audit log.** Operational tuning,
  recoverable from flags. Revisit if reproducibility audits later need it.
