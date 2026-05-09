# Configuration

kuroi resolves configuration from four sources, applied in order. Later
sources override earlier ones.

1. **Built-in defaults.**
2. **`$XDG_CONFIG_HOME/kuroi/config.toml`** (or `~/.config/kuroi/config.toml`).
3. **Environment variables.**
4. **CLI flags** (mostly on `kuroi run`).

The merged result is an immutable `Config` object defined in
`src/kuroi/core/config.py`.

## Configuration keys

| Key                           | Type     | Default                       | TOML location                          | CLI flag                       | Env var                            |
| ----------------------------- | -------- | ----------------------------- | -------------------------------------- | ------------------------------ | ---------------------------------- |
| `provider`                    | string   | `anthropic`                   | top-level `provider`                   | `--provider`                   | `KUROI_PROVIDER`                   |
| `model`                       | string   | `claude-opus-4-7` (anthropic) | top-level `model`                      | `--model`                      | `KUROI_MODEL`                      |
| `ollama_url`                  | string   | `http://localhost:11434`      | `[ollama]` table, `url`                | `--ollama-url`                 | `KUROI_OLLAMA_URL`                 |
| `audit_include_text`          | bool     | `false`                       | `[audit]` table, `include_text`        | (file only)                    | (none)                             |
| `backup_retention_hours`      | int      | `24`                          | `[backup]` table, `retention_hours`    | (file only)                    | (none)                             |
| `retry.max_retries`           | int      | `2`                           | `[retry]` table, `max_retries`         | `--max-retries`                | `KUROI_MAX_RETRIES`                |
| `retry.backoff`               | float    | `2.0`                         | `[retry]` table, `backoff`             | `--retry-backoff`              | `KUROI_RETRY_BACKOFF`              |
| `retry.backoff_multiplier`    | float    | `2.0`                         | `[retry]` table, `backoff_multiplier`  | `--retry-backoff-multiplier`   | `KUROI_RETRY_BACKOFF_MULTIPLIER`   |
| `prompt.layout_aware`         | bool     | `false`                       | `[prompt]` table, `layout_aware`       | `--layout-aware` / `--no-layout-aware` | (none)                   |
| `claude_cli.cli_path`         | string   | (unset)                       | `[claude_cli]` table, `cli_path`       | `--claude-cli-path`            | (none)                             |
| `claude_cli.timeout_s`        | int      | `300`                         | `[claude_cli]` table, `timeout_s`      | `--claude-cli-timeout`         | (none)                             |
| `ANTHROPIC_API_KEY`           | string   | (unset)                       | (not in TOML)                          | (env only)                     | `ANTHROPIC_API_KEY`                |

When the provider is `ollama`, there is no built-in default model — you
must set `model` explicitly (via CLI flag, env var, file, or
`kuroi setup`).

## Editing the config file

The on-disk format is TOML. A flat top level for `provider`/`model`
plus optional `[ollama]`, `[audit]`, `[backup]`, and `[retry]` tables.

```toml
provider = "anthropic"
model = "claude-opus-4-7"

[ollama]
url = "http://localhost:11434"

[audit]
include_text = false

[backup]
retention_hours = 24

[retry]
max_retries = 2
backoff = 2.0
backoff_multiplier = 2.0

[prompt]
layout_aware = false

[claude_cli]
# cli_path = "/usr/local/bin/claude"  # default: bundled CLI from claude-agent-sdk
# timeout_s = 300
```

`kuroi setup` writes the top-level `provider`/`model` keys and the
`[ollama]` table interactively. `audit.include_text`,
`backup.retention_hours`, and the `[retry]` table are not exposed by
`setup` — edit `config.toml` directly to change them.

The `[claude_cli]` table only applies when `provider = "claude-cli"`.
`cli_path` lets you override the bundled `claude` binary (default: the one
shipped with `claude-agent-sdk`); `timeout_s` caps each `query()` call.
The CLI flags are `--claude-cli-path` and `--claude-cli-timeout`.

## Retry policy

The `[retry]` table tunes how the orchestrator handles transient provider
failures (HTTP errors, timeouts, malformed responses). Defaults reproduce
the historical schedule of 2s then 4s before giving up.

The schedule formula is:

> delay before retry *k* (1-indexed) = `backoff × backoff_multiplier^(k-1)`

Examples:

- `--max-retries 0` — fail fast; abort on the first hard failure.
- `--max-retries 5 --retry-backoff 1 --retry-backoff-multiplier 2` —
  schedule `(1, 2, 4, 8, 16)` for slow Ollama or rate-limited APIs.
- `--retry-backoff-multiplier 1` — fixed delay between retries.

Validation rules: `max_retries >= 0`, `backoff >= 0`,
`backoff_multiplier >= 1.0`.

## `[prompt]`

Prompt-shaping options.

| Key            | Type    | Default | Description                                                                 |
|----------------|---------|---------|-----------------------------------------------------------------------------|
| `layout_aware` | boolean | `false` | Wrap the LLM prompt with PyMuPDF block boundaries. See [Prompt tuning](../user-guide/prompt-tuning.md). |

CLI flag override: `--layout-aware` / `--no-layout-aware`.

## Refreshing pricing

```sh
$ kuroi config refresh-pricing --from path/to/pricing.json
```

Loads the per-token pricing JSON and caches it under
`$XDG_DATA_HOME/kuroi/pricing.json`. The cached file is what
`kuroi.core.pricing.estimate_cost` reads at runtime.
