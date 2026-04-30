# LLM providers

kuroi uses an LLM as a "judge" to spot context-sensitive redactions that
plain regex misses. Two providers ship with kuroi.

## Provider comparison

| Provider | Hosting | API key required | Cost | Best for |
| --- | --- | --- | --- | --- |
| **Anthropic** | Cloud | Yes (`ANTHROPIC_API_KEY`) | Per-token | Highest-quality detection on small batches. |
| **Ollama** | Local | No | Free (your hardware) | Offline workflows; sensitive data that must not leave the host. |

## List available models

```sh
$ kuroi models
Anthropic
  claude-opus-4-7         (default)
  claude-sonnet-4-6
  claude-haiku-4-5

Ollama (http://localhost:11434)
  llama3.1:8b
  llama3.1:70b
```

If Ollama is not running, the Ollama section reports `unreachable`.

## Configure a provider

=== "Anthropic"

    ```sh
    $ export ANTHROPIC_API_KEY=sk-ant-...
    $ kuroi run document.pdf --provider anthropic --model claude-opus-4-7
    ```

    Or persist it:

    ```sh
    $ kuroi config set provider anthropic
    $ kuroi config set model claude-opus-4-7
    ```

=== "Ollama"

    ```sh
    $ ollama serve &  # start the daemon if it's not already running
    $ ollama pull llama3.1:8b
    $ kuroi run document.pdf --provider ollama --model llama3.1:8b
    ```

    Or persist it:

    ```sh
    $ kuroi config set provider ollama
    $ kuroi config set model llama3.1:8b
    $ kuroi config set ollama_url http://localhost:11434
    ```

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
3. `kuroi config set provider ollama && kuroi config set model llama3.1:8b`.
4. Run `kuroi run document.pdf`. No outbound HTTP except to `localhost`.

## Switching providers mid-project

You can change provider per-invocation without losing state. The audit
record stores which provider produced each finding:

```sh
$ kuroi run document.pdf --provider anthropic
$ kuroi diff document.pdf | head
  page 3, words 12-13   email           anthropic    high
  page 3, word 88       phone           rules:pii-en high
  ...
```

## Next steps

- [Audit & undo](audit-and-undo.md) — verify a redaction independently of
  the provider that produced it.
- [Adding an LLM provider](../developer-guide/adding-a-provider.md) — write
  a custom backend for a self-hosted model.
