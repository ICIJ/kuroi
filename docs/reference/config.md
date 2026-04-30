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

| Key                          | Type     | Default                       | TOML location                       | CLI flag         | Env var              |
| ---------------------------- | -------- | ----------------------------- | ----------------------------------- | ---------------- | -------------------- |
| `provider`                   | string   | `anthropic`                   | top-level `provider`                | `--provider`     | `KUROI_PROVIDER`     |
| `model`                      | string   | `claude-opus-4-7` (anthropic) | top-level `model`                   | `--model`        | `KUROI_MODEL`        |
| `ollama_url`                 | string   | `http://localhost:11434`      | `[ollama]` table, `url`             | `--ollama-url`   | `KUROI_OLLAMA_URL`   |
| `audit_include_text`         | bool     | `false`                       | `[audit]` table, `include_text`     | (file only)      | (none)               |
| `backup_retention_hours`     | int      | `24`                          | `[backup]` table, `retention_hours` | (file only)      | (none)               |
| `ANTHROPIC_API_KEY`          | string   | (unset)                       | (not in TOML)                       | (env only)       | `ANTHROPIC_API_KEY`  |

When the provider is `ollama`, there is no built-in default model — you
must set `model` explicitly (via CLI flag, env var, file, or
`kuroi setup`).

## Editing the config file

The on-disk format is TOML. A flat top level for `provider`/`model`
plus optional `[ollama]`, `[audit]`, and `[backup]` tables.

```toml
provider = "anthropic"
model = "claude-opus-4-7"

[ollama]
url = "http://localhost:11434"

[audit]
include_text = false

[backup]
retention_hours = 24
```

`kuroi setup` writes the top-level `provider`/`model` keys and the
`[ollama]` table interactively. `audit.include_text` and
`backup.retention_hours` are not exposed by `setup` — edit
`config.toml` directly to change them.

## Refreshing pricing

```sh
$ kuroi config refresh-pricing --from path/to/pricing.json
```

Loads the per-token pricing JSON and caches it under
`$XDG_DATA_HOME/kuroi/pricing.json`. The cached file is what
`kuroi.core.pricing.estimate_cost` reads at runtime.
