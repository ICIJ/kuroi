# Claude CLI provider — design

**Date:** 2026-05-09
**Status:** Approved (brainstorming complete; pending implementation plan)

## Goal

Add a third LLM provider, `claude-cli`, that drives the local `claude` binary
through Anthropic's `claude-agent-sdk` Python package. Calls bill against the
user's Claude Code subscription instead of the per-token Anthropic API,
removing the need for `ANTHROPIC_API_KEY` for users who already pay for a
subscription.

## Non-goals

- Replacing or refactoring the existing `AnthropicProvider`. It stays as the
  per-token API path.
- Changing the `Provider` Protocol. `ClaudeCliProvider` matches the existing
  signature exactly.
- Multi-provider abstraction layers (no `ai-sdk-python`, no internal
  unification).
- Streaming or agentic behavior. The CLI is invoked single-turn, no tools,
  no loop.
- Prompt caching. The CLI does not surface ephemeral cache controls, and
  on subscription billing there is no per-token cost to cache against.

## Why this approach

The original ask was framed around `ai-sdk-python`. Brainstorming surfaced
that ai-sdk-python's `anthropic()` provider hits `https://api.anthropic.com`
with an API key — the exact thing the user wanted to avoid. The path that
actually reaches subscription billing is the local `claude` CLI binary, and
the cleanest Python interface for that binary is Anthropic's official
`claude-agent-sdk`.

Direct subprocess (no SDK) was considered; it works, but `claude-agent-sdk`
provides typed errors (`CLINotFoundError`, `ProcessError`,
`CLIJSONDecodeError`, `CLIConnectionError`) that map cleanly to kuroi's
existing hard-fail vs soft-fail split, and Anthropic maintains the SDK so
CLI surface drift is absorbed there instead of in our code.

## Architecture

### File layout

- `src/kuroi/providers/claude_cli.py` — the new `ClaudeCliProvider` class
  (~150 lines, modeled on `providers/ollama.py`).
- `src/kuroi/providers/factory.py` — third dispatch arm for
  `provider == "claude-cli"`.
- `src/kuroi/core/config.py` — widen `ProviderName` literal and
  `VALID_PROVIDERS` tuple; add optional `[claude_cli]` config table.
- `src/kuroi/cli/run.py` — add `--claude-cli-path` flag.
- `pyproject.toml` — add `claude-agent-sdk>=0.1.80` to `dependencies`.

### Async ↔ sync bridge

`claude-agent-sdk` is async-only (`anyio`-based). kuroi's
`Provider.detect_redactions` is synchronous. Each call wraps an
`anyio.run(self._aexec, ...)`. The event-loop spin-up cost (~1 ms) is
negligible compared to the LLM round-trip (seconds), and this keeps the
existing concurrency model — per-batch model-group dispatch from the recent
chunking refactor (commits `e589479`, `4315bc2`, `f8c6894`, `08f0146`,
`2a11e18`) — untouched.

### Lazy import

`from claude_agent_sdk import query, ClaudeAgentOptions, ...` happens inside
the class (constructor and method bodies), mirroring how `anthropic.py` does
`import anthropic` locally. This keeps the SDK out of the import graph for
test environments that stub `query_fn` and lets the test suite run without
the SDK installed.

## Provider class

### Signature

```python
class ClaudeCliProvider:
    name = "claude-cli"

    def __init__(
        self,
        *,
        model: str = "claude-opus-4-7",
        cli_path: str | None = None,   # None → bundled CLI
        timeout_s: int = 300,
        query_fn: Any | None = None,    # injection point for tests
    ) -> None: ...

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        instructions: tuple[str, ...] = (),
        seed: int | None = None,
        attempt: int = 0,
        layout_aware: bool = False,
        model: str | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]: ...
```

### Per-call model override (per-rule routing)

The chunking layer partitions categories by model and dispatches model
groups concurrently per batch (`08f0146`, `e589479`, `4315bc2`). It does
this by calling `provider.detect_redactions(..., model=<per-group model>)`.
`AnthropicProvider` honors the override via `effective_model = model or
self.model` (`anthropic.py:127`).

`ClaudeCliProvider` adopts the identical pattern. The effective model is
passed to `ClaudeAgentOptions(model=effective_model)` for that single async
call. Rule packs that already declare per-category `model:` fields work
unchanged when the active provider is `claude-cli`.

### Prompt construction

Reuse existing helpers in `providers/_shared.py`:

- `build_user_static_prefix(llm_category_ids, instructions)` — cacheable
  prefix string.
- `build_user_document_block(pages, layout_aware=...)` — `<document>`-tagged
  body string.
- `build_system_blocks(layout_aware)` — system blocks; flatten the list of
  dicts to a single string for the SDK's `system_prompt=` parameter (the
  CLI's `--system-prompt` is a string, not blocks).

The user prompt sent to `query()` is `static_prefix + document_block`. The
prompt-hash field on the audit record is computed over the same
concatenation, matching the Anthropic provider.

We do **not** use the CLI's `--json-schema` flag. Reasons: the existing
prompt instructs JSON output, `parse_findings_payload` tolerates noisy
responses, and the SDK's exposure of that flag is uncertain at v0.1.80.
Consistency with the Anthropic and Ollama providers wins here.

### Silencing the agent loop

`ClaudeAgentOptions` configured per call:

| Option | Value | Why |
|---|---|---|
| `system_prompt` | flattened kuroi system | Replaces Claude Code's coding system prompt entirely. |
| `model` | effective model | Per-call override; otherwise `self.model`. |
| `max_turns` | `1` | Single exchange, no agent loop. |
| `allowed_tools` | `[]` | Empty allowlist. |
| `disallowed_tools` | `["*"]` if wildcard supported, else best-effort enumerated list | Defense-in-depth on top of empty `allowed_tools`. |
| `permission_mode` | `"default"` | The CLI documents `acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`. `default` blocks tool use in non-interactive (`--print`) mode because there is no TTY for the permission prompt. Combined with `allowed_tools=[]`, no tool ever runs. |
| `setting_sources` | `[]` | Don't load CLAUDE.md, project settings, user settings, hooks. |
| `cli_path` | from config | `None` → bundled CLI shipped with the SDK. |

### Reading the response

Iterate the `query()` async iterator:

- `AssistantMessage` events: concatenate every `TextBlock.text` into
  `result_text`.
- `ResultMessage` (terminator): grab `usage.input_tokens`,
  `usage.output_tokens`, and (if exposed) `duration_ms`.

Feed `result_text` to `parse_findings_payload(payload, pages, source=...)`,
identical to the Anthropic and Ollama paths. `source = "instruction" if not
llm_category_ids else "llm"`.

## Authentication

The bundled (or system) `claude` CLI authenticates via:

1. `ANTHROPIC_API_KEY` env var — if set, takes precedence (per-token API
   billing).
2. OAuth / subscription credentials (typically `~/.claude/credentials.json`).

The user's goal is subscription billing. We do **not** strip
`ANTHROPIC_API_KEY` from the subprocess environment. Instead, the provider
constructor checks `os.environ` and emits a one-line `WARNING`-level log
when the var is set:

> `claude-cli provider: ANTHROPIC_API_KEY is set in your environment, so the
> CLI may use API (per-token) billing instead of your subscription. Unset
> the variable if you want subscription billing.`

The user retains control. The decision is recorded in the audit chunk only
implicitly (via `tokens_in/tokens_out` and the absence of cache token
fields).

## Error handling

Map `claude-agent-sdk` exceptions to kuroi's existing two-tier model:

| SDK error | kuroi response |
|---|---|
| `CLINotFoundError` | Raise `ConfigError("Claude CLI not found. Install with `pip install claude-agent-sdk` or `npm install -g @anthropic-ai/claude-code`, then run `claude /login` to authenticate.")`. |
| `ProcessError` whose message matches `r"(?i)not authenticated\|no credentials\|please log in"` | Raise `ConfigError("Claude CLI is not authenticated. Run `claude /login` to log in with your subscription account.")`. |
| `ProcessError` (other) | Log `WARNING`, return `([], [chunk])`. Chunk preserves prompt SHA for replay. |
| `CLIJSONDecodeError` | Log `WARNING`, return `([], [chunk])`. |
| `CLIConnectionError` | Log `WARNING`, return `([], [chunk])` (likely transient). |
| Timeout (via `anyio.fail_after(timeout_s)`) | Log `WARNING`, return `([], [chunk])`. |
| Unexpected `Exception` | Re-raise. |

This mirrors `AnthropicProvider`'s split between hard `ConfigError` (user
must fix config) and soft `([], [chunk])` (chunking layer subdivides and
retries).

## Audit record mapping

Each `detect_redactions` call emits exactly one `ChunkRecord` even on
soft-fail paths.

| `ChunkRecord` field | Source |
|---|---|
| `chunk_idx` | `0` |
| `pages` | `tuple(p.number for p in pages)` |
| `temperature` | `0.0` (declared intent; CLI accepts no temperature flag) |
| `seed_requested` | passed-through `seed` arg |
| `seed_honored` | `False` |
| `system_fingerprint` | `None` |
| `prompt_sha256` | SHA-256 over `static_prefix + document_block` |
| `response_sha256` | SHA-256 over the concatenated `TextBlock` text |
| `tokens_in` | `ResultMessage.usage.input_tokens` (or `0`) |
| `tokens_out` | `ResultMessage.usage.output_tokens` (or `0`) |
| `duration_ms` | `int((time.monotonic() - started) * 1000)` around `anyio.run` |
| `cache_creation_input_tokens` | `0` (not surfaced by CLI envelope) |
| `cache_read_input_tokens` | `0` |

## Pricing & `kuroi models` listing

No entry added to `core/pricing.py` for `("claude-cli", *)`.
`estimate_cost` already returns `$0` for missing rates, which is the
correct value for subscription billing. Add a one-line code comment in
`pricing.py` next to the existing tables explaining the omission.

`kuroi models` output gains a third section:

```
Claude CLI                                                  subscription
  claude-opus-4-7        (default)   subscription billing
  claude-sonnet-4-6                  subscription billing
  claude-haiku-4-5-20251001          subscription billing
  seed support: not available
```

Same three model IDs as the Anthropic provider — they are identical models,
routed differently. The `kuroi models claude-cli` filter and `--json` mode
work the same way as for other providers.

## Configuration

### Config file (`~/.config/kuroi/config.toml`)

```toml
provider = "claude-cli"
model = "claude-opus-4-7"

[claude_cli]
# Optional. Defaults to the CLI bundled with claude-agent-sdk.
cli_path = "/usr/local/bin/claude"
# Optional. Default 300 seconds.
timeout_s = 300
```

### Type changes (`core/config.py`)

```python
ProviderName = Literal["anthropic", "ollama", "claude-cli"]
VALID_PROVIDERS: tuple[ProviderName, ...] = (
    "anthropic", "ollama", "claude-cli",
)
```

Add to `Config`:
- `claude_cli_path: str | None = None`
- `claude_cli_timeout_s: int = 300`

Read from TOML via `_read_string("claude_cli.cli_path")` and
`_read_int("claude_cli.timeout_s")`. Add corresponding fields to
`ConfigOverrides` for the CLI flag.

### Factory (`providers/factory.py`)

```python
if config.provider == "claude-cli":
    return ClaudeCliProvider(
        model=config.model,
        cli_path=config.claude_cli_path,
        timeout_s=config.claude_cli_timeout_s,
    )
```

### `kuroi setup` (interactive)

Provider picker gains a third option:

> "Claude CLI (uses your Claude Code subscription)"

When selected, run a probe: `anyio.run(probe_claude_cli)` which calls
`query(prompt="ping", options=ClaudeAgentOptions(max_turns=1,
system_prompt="Reply with exactly: pong", allowed_tools=[],
setting_sources=[]))` and verifies a `ResultMessage` arrives.

- `CLINotFoundError`: print install instructions, do not write config.
- `ProcessError` with auth marker: print `Run "claude /login" then re-run
  "kuroi setup"`, do not write config.
- Success: write `provider = "claude-cli"` to the config and offer the
  model picker.

## Testing

`tests/providers/test_claude_cli.py`, modeled on
`tests/providers/test_ollama.py`. The provider takes a `query_fn` injection
point; tests pass a stub async generator so the SDK does not need to be
installed in CI to run unit tests.

Cases:

- **Happy path** — stub yields valid JSON in `TextBlock`. Assert `Finding`s
  parsed; `ChunkRecord` has correct `tokens_in`, `tokens_out`,
  `prompt_sha256`, `response_sha256`.
- **No LLM categories, no instructions** — returns `([], [])` without
  invoking the stub.
- **Malformed JSON** — stub yields `TextBlock(text="not json")`. Assert
  `([], [chunk])`; chunk SHAs still recorded.
- **`CLINotFoundError`** — stub raises it. Assert `ConfigError` with
  install-instruction message.
- **`ProcessError` with auth marker** — assert `ConfigError` with `claude
  /login` message.
- **`ProcessError` (other)** — assert `([], [chunk])`.
- **`CLIJSONDecodeError`** — assert `([], [chunk])`.
- **Timeout** — stub `await anyio.sleep_forever()`; assert `([], [chunk])`
  after `timeout_s`.
- **Per-call model override** — call with `model="claude-haiku-4-5-20251001"`
  while `self.model="claude-opus-4-7"`; assert `ClaudeAgentOptions.model`
  passed to the stub equals the override (mirrors the two-model routing
  test at `2a11e18`).
- **Options assertions** — assert the options handed to the stub include
  `max_turns=1`, `allowed_tools=[]`, `permission_mode="default"`,
  `setting_sources=[]`, `system_prompt` is the flattened kuroi system, and
  `model` is the effective model. (The exact `disallowed_tools` value
  depends on whether `["*"]` is honored; assert what we actually pass.)
- **`ANTHROPIC_API_KEY` warning** — set the env var, instantiate the
  provider, assert a `WARNING` record is logged (use `caplog`).

Integration smoke test gated with
`@pytest.mark.skipif(not shutil.which("claude"), reason="...")` and
`@pytest.mark.slow`; runs an actual `query()` against a one-page document.
Excluded from default `make test`.

No changes to existing Anthropic / Ollama tests.

## Documentation

### `docs/user-guide/providers.md`

Add a third row to the comparison table:

| Provider       | Hosting | API key required        | Cost                | Best for                           |
|----------------|---------|-------------------------|---------------------|------------------------------------|
| Anthropic      | Cloud   | Yes (`ANTHROPIC_API_KEY`) | Per-token         | Highest-quality on small batches.  |
| Claude CLI     | Cloud   | No (subscription)       | Subscription        | Heavy use under a Claude Code plan.|
| Ollama         | Local   | No                      | Free (your hardware)| Offline / sensitive data.          |

Add a new `=== "Claude CLI"` tab with:

```sh
$ claude /login           # one-time, authenticates your subscription
$ kuroi run document.pdf --provider claude-cli --model claude-opus-4-7
```

Plus the `[claude_cli]` config block, a one-paragraph note that
`ANTHROPIC_API_KEY` shadows subscription auth at the CLI level (and that
kuroi will warn you if it's set), and a one-paragraph reference to per-rule
`model:` overrides working unchanged.

### `docs/reference/config.md`

Add `claude_cli.cli_path` and `claude_cli.timeout_s` keys.

### `CHANGELOG.md`

Under the unreleased section, add:

> Add `claude-cli` provider that routes through the local Claude CLI
> (subscription billing, no API key required). Per-rule `model:` overrides
> apply to the new provider on the same terms as the Anthropic provider.

### `docs/developer-guide/adding-a-provider.md`

No changes. The guide stays generic, using Ollama as the worked example.

## Open questions deferred to implementation

- Exact field names on `ResultMessage.usage` (`input_tokens` vs
  `inputTokens` etc.) — verify against `claude-agent-sdk` v0.1.80 source
  at implementation time and adjust if needed.
- Whether `ClaudeAgentOptions.disallowed_tools=["*"]` is honored as a
  wildcard. If not, fall back to `allowed_tools=[]` only (combined with
  `permission_mode="default"`, no tool can run in non-interactive mode).
- Whether the SDK's `query()` result-message exposes `duration_ms`
  directly. If not, time the `anyio.run` call from the outside, which is
  what the spec already does — drop any in-message duration field.
- Whether `setting_sources=[]` is the exact parameter name in
  `ClaudeAgentOptions` v0.1.80, and whether the empty-list semantics match
  "load nothing." If the parameter name differs, use the equivalent
  documented option for "do not load user/project/local settings."
