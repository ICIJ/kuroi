# LLM providers

kuroi uses an LLM as a "judge" to spot context-sensitive redactions that
plain regex misses. Two providers ship with kuroi.

## Provider comparison

| Provider | Hosting | API key required | Cost | Best for |
| --- | --- | --- | --- | --- |
| **Anthropic** | Cloud | Yes (`ANTHROPIC_API_KEY`) | Per-token | Highest-quality detection on small batches. |
| **Claude CLI** | Cloud | No (subscription) | Subscription | Heavy use under a Claude Code plan. |
| **Ollama** | Local | No | Free (your hardware) | Offline workflows; sensitive data that must not leave the host. |

## List available models

```sh
$ kuroi models

Anthropic                                                   cloud
  claude-opus-4-7        (default)   $15.00 / $75.00 per Mtok
  claude-sonnet-4-6                  $3.00 / $15.00 per Mtok
  claude-haiku-4-5-20251001          $1.00 / $5.00 per Mtok
  seed support: temperature=0 only (best-effort, recorded in audit)

Claude CLI                                                  cloud
  claude-opus-4-7                    subscription billing
  claude-sonnet-4-6                  subscription billing
  claude-haiku-4-5-20251001          subscription billing
  seed support: not available

Ollama                                                      local
  llama3.1:8b            (installed) free
  llama3.1:70b                       free
  seed support: full

Default: anthropic / claude-opus-4-7   (configurable)
Pricing last updated: 2026-04-01
```

`(installed)` is shown for Ollama models the local daemon currently
serves; if the Ollama daemon at the configured URL isn't reachable,
the column is simply blank. Use `kuroi models --json` for machine-readable
output, or `kuroi models ollama` to filter to one provider.

## Configure a provider

=== "Anthropic"

    Pass per invocation:

    ```sh
    $ export ANTHROPIC_API_KEY=sk-ant-...
    $ kuroi run document.pdf --provider anthropic --model claude-opus-4-7
    ```

    Or persist by editing `~/.config/kuroi/config.toml`:

    ```toml
    provider = "anthropic"
    model = "claude-opus-4-7"
    ```

    `kuroi setup` will write this file for you interactively.

=== "Claude CLI"

    Pass per invocation:

    ```sh
    $ pip install claude-agent-sdk          # or `npm install -g @anthropic-ai/claude-code`
    $ claude /login                          # one-time, authenticates your subscription
    $ kuroi run document.pdf --provider claude-cli --model claude-opus-4-7
    ```

    Or persist by editing `~/.config/kuroi/config.toml`:

    ```toml
    provider = "claude-cli"
    model = "claude-opus-4-7"

    # Optional — both fields default sensibly.
    [claude_cli]
    cli_path = "/usr/local/bin/claude"
    timeout_s = 300
    ```

    `kuroi setup` will probe the CLI, verify it's authenticated, and write
    this file for you.

    !!! note "ANTHROPIC_API_KEY shadowing"
        If `ANTHROPIC_API_KEY` is set in your environment when you select the
        `claude-cli` provider, the Claude CLI will prefer API (per-token)
        billing over your subscription. kuroi prints a warning at startup so
        the behavior is visible. Unset the variable to force subscription
        billing.

    Per-rule `model:` overrides (in your rule packs or category YAML) work
    the same way as for the Anthropic provider — the chunking layer dispatches
    per-model groups concurrently per batch:

    ```yaml
    - id: contact_info
      llm: true
      model: claude-haiku-4-5-20251001
    ```

=== "Ollama"

    Pass per invocation:

    ```sh
    $ ollama serve &  # start the daemon if it's not already running
    $ ollama pull llama3.1:8b
    $ kuroi run document.pdf --provider ollama --model llama3.1:8b
    ```

    Or persist by editing `~/.config/kuroi/config.toml`:

    ```toml
    provider = "ollama"
    model = "llama3.1:8b"

    [ollama]
    url = "http://localhost:11434"
    ```

    `kuroi setup` will probe the daemon, list installed models, and write
    this file for you.

## Configuration precedence

kuroi resolves configuration in this order (later overrides earlier):

1. Built-in defaults.
2. `~/.config/kuroi/config.toml` (or `$XDG_CONFIG_HOME/kuroi/config.toml`).
3. Environment variables (`ANTHROPIC_API_KEY`, `KUROI_PROVIDER`, `KUROI_MODEL`).
4. CLI flags (`--provider`, `--model`, `--ollama-url`).

See [Configuration](../reference/config.md) for the full key list.

## Fully offline workflow

1. Install [Ollama](https://ollama.ai).
2. Pull a model: `ollama pull llama3.1:8b`.
3. Run `kuroi setup` and pick `ollama` + your model, or write
   `~/.config/kuroi/config.toml` by hand (see the Ollama tab above).
4. Run `kuroi run document.pdf`. No outbound HTTP except to `localhost`.

## Switching providers mid-project

You can change provider per-invocation without persisting the choice. The
audit record stores which provider produced each finding, so you can tell
later which run did what:

```sh
$ kuroi run document.pdf --provider anthropic
$ kuroi diff document.pdf document.redacted.pdf --format json | head -1
{"page":3,"before_text":"...","after_text":"...","redactions":[{"bbox":[40,120,180,138],"kind":"","replacement":"","before_text":"j.doe@example.com"}]}
```

For the per-finding `provider` and `kind`, read the audit JSONL directly
(see [Audit & undo](audit-and-undo.md)).

## Next steps

- [Audit & undo](audit-and-undo.md) — verify a redaction independently of
  the provider that produced it.
- [Adding an LLM provider](../developer-guide/adding-a-provider.md) — write
  a custom backend for a self-hosted model.
