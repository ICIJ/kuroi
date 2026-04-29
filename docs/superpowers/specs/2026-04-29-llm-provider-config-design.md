# LLM Provider Configuration Design

**Status:** Draft, awaiting user review.
**Date:** 2026-04-29

## Goal

Let users choose between the existing Anthropic provider and a new Ollama provider, configure the model and (for Ollama) the daemon URL, and persist those choices either via CLI flags, environment variables, or an XDG-compliant config file. Add a `kuroi setup` interactive command that writes the config file.

## Non-goals

- Profiles. The on-disk schema reserves room for them; no profile UI ships now.
- Live network tests in CI for either provider.
- Fallback between providers. If the chosen provider fails, `run` reports it; it does not silently switch.
- Probing or test calls from `kuroi run`. Health checks live in `kuroi doctor`.
- Provider-specific tunables beyond what's needed to issue a request (no temperature, top-p, custom system prompts, etc.).

## User-facing surface

### Configuration sources, in precedence order

1. CLI flags on `kuroi run` (`--provider`, `--model`, `--ollama-url`)
2. Environment variables (`KUROI_PROVIDER`, `KUROI_MODEL`, `KUROI_OLLAMA_URL`; `ANTHROPIC_API_KEY` is read directly by the Anthropic provider and is not part of `Config`)
3. Config file at `$XDG_CONFIG_HOME/kuroi/config.toml` (default `~/.config/kuroi/config.toml`)
4. Built-in defaults: `provider = "anthropic"`, `model = "claude-opus-4-7"` for Anthropic, `ollama.url = "http://localhost:11434"`. There is no built-in default model for Ollama.

Resolution is per-key. Each key takes the highest-precedence source that has a value for it. Mixed sources are valid (e.g. provider from file, model from CLI).

### Config file format

TOML, flat keys for the active selection, provider-specific tables for provider-specific settings:

```toml
provider = "ollama"
model    = "llama3.1:8b"

[ollama]
url = "http://localhost:11434"
```

Read with stdlib `tomllib`. Written by a small in-package serializer (the on-disk shape is a flat document with one nested table; no general TOML writer needed). Writes go to `config.toml.tmp` then `os.replace()` for atomicity.

The schema is designed so profile support can be added later without breaking existing config files: a future profile-aware loader can treat top-level scalars as the implicit default profile.

### `kuroi setup`

Interactive Typer subcommand. Reads the existing config file (if any) and uses its values as defaults so re-runs feel like editing.

Flow:

1. Prompt for provider with current value as default.
2. **Anthropic branch:** offer a curated short list of model IDs plus "type a custom name". Warn if `ANTHROPIC_API_KEY` is unset; do not block. Make no billed test call.
3. **Ollama branch:** prompt for URL with current value as default. `GET {url}/api/tags` with a short timeout (~3s):
   - **Reachable:** present installed models as a numbered picker plus "type a custom name".
   - **Unreachable:** print a soft warning and fall back to free-text model entry. The user may legitimately be configuring an Ollama URL that's currently down.
4. Write the config file. Print the path.

`setup` only edits the file. CLI overrides and env vars are not consulted; they only affect `run`.

### `kuroi run` flag changes

- `--model` default changes from `"claude-opus-4-7"` to `None`. The default is now layered into `resolve_config`.
- New `--provider` flag (`anthropic` or `ollama`).
- New `--ollama-url` flag.

The rest of `run` is unchanged. It builds a `ConfigOverrides` object from the flags it received, calls `resolve_config()`, calls `make_provider()`, and proceeds exactly as before.

### `kuroi doctor` extensions

- Print the resolved `provider` and `model` (and `ollama.url` if relevant).
- If the resolved provider is Ollama, report whether the URL is reachable (read-only `GET /api/tags`). Don't call any model.
- Existing doctor checks remain.

## Internal architecture

### New modules

- `kuroi.core.config`
  - `Config` — frozen dataclass: `provider: Literal["anthropic", "ollama"]`, `model: str`, `ollama_url: str`. After successful resolution `model` is always populated; the `None` case is transient within `resolve_config` and triggers the "no Ollama model configured" validation error before a `Config` is constructed.
  - `ConfigOverrides` — frozen dataclass with `provider | model | ollama_url: str | None`. Built from CLI flags.
  - `ConfigError` — single exception type for resolution failures.
  - `resolve_config(overrides: ConfigOverrides, *, env: Mapping[str, str], file_path: Path) -> Config` — pure function. Reads the file (if it exists) via `tomllib`, walks the precedence chain key-by-key, validates the result.
  - `load_config_file(path: Path) -> dict[str, Any]` and `write_config_file(path: Path, config: Config) -> None`.
  - `xdg_config_home() -> Path` — honors `XDG_CONFIG_HOME`, falls back to `~/.config`.
- `kuroi.providers.factory`
  - `make_provider(config: Config) -> Provider` — dispatches on `config.provider`. Single import point for provider classes; the CLI never imports them directly.
- `kuroi.providers.ollama`
  - `OllamaProvider` implementing the existing `Provider` Protocol. Uses `httpx` to `POST {url}/api/chat` with the Ollama JSON body (model, messages, `format: "json"`, `stream: false`). Tests inject a stub HTTP client via constructor argument, mirroring `AnthropicProvider(client=...)`.
- `kuroi.providers._shared`
  - `parse_findings_payload` is moved here from `anthropic.py`. Both providers import it. The Anthropic module re-imports it; behavior is unchanged.
- `kuroi.cli.setup`
  - `setup_app` Typer subcommand registered in `cli/__init__.py`.

### Changes to existing modules

- `cli/run.py`: drop the direct `AnthropicProvider` import; build a `ConfigOverrides` from new flags; call `resolve_config()` and `make_provider()`. The audit-log fields (`provider`, `model`) come from the resolved provider just as they do today.
- `cli/__init__.py`: register `setup_app`.
- `cli/doctor.py`: extend to show resolved config and check Ollama reachability when the resolved provider is Ollama.
- `providers/anthropic.py`: `parse_findings_payload` is removed and re-imported from `providers/_shared.py`. No behavioral change.
- `pyproject.toml`: add `httpx` to `dependencies`.

### Validation rules in `resolve_config`

- Provider value must be in `{"anthropic", "ollama"}`. Else `ConfigError("Unknown provider: <value>")`.
- If the resolved provider is `"ollama"` and `model` is `None`, raise `ConfigError("No Ollama model configured. Run \`kuroi setup\` or pass --model.")`. Anthropic falls back to its built-in default.
- Config file with invalid TOML → `ConfigError` referencing the file path and the underlying `tomllib` message.
- Config file with wrong types — at any key, including nested ones (`provider`, `model`, `ollama.url`) — raises `ConfigError` naming the key and the observed type. Example: `ConfigError("Expected string for `provider`, got int")`.

## Data flow

### `kuroi run`

1. Typer parses flags → `ConfigOverrides`. Unset flags are `None`.
2. `resolve_config(overrides, env=os.environ, file_path=xdg_config_home() / "kuroi" / "config.toml")` returns a `Config` or raises `ConfigError`.
3. `cli/run.py` catches `ConfigError`, prints in red, exits `2`.
4. `make_provider(config)` returns a `Provider`.
5. The rest of `run()` is unchanged.

### `kuroi setup`

1. Resolve current values from the file only (env and CLI ignored).
2. Prompt loop as described above.
3. Build a `Config`, write atomically (`config.toml.tmp` → `os.replace()`).
4. Print the resolved path.

## Error handling

### Config-resolution errors (raised from `resolve_config`)

Single exception type, `ConfigError`, surfaced as an exit code `2` with a red message in `cli/run.py` and `cli/setup.py`. Cases enumerated above under "Validation rules".

### Ollama runtime errors

`OllamaProvider.detect_redactions` returns an empty findings list — and writes nothing to stderr — when any of the following occur:

- `httpx.ConnectError` (daemon down or wrong URL)
- `httpx.TimeoutException` (connect 5s, read 120s)
- Non-2xx response
- JSON decode failure on the response body
- Missing `message.content` field
- `parse_findings_payload` rejects all findings as malformed

This matches the existing Anthropic provider's behavior on JSON decode failure: a degraded LLM signal does not block deterministic regex findings, and post-redaction verification still gates every write. The audit log records the chosen provider and model regardless.

No retries. The user can re-run.

### Setup-time errors

- Ollama probe failure during `/api/tags` is not an error — it's a soft warning that downgrades the model picker to free-text.
- Missing `ANTHROPIC_API_KEY` during setup is a soft warning.
- Config-file write failure (permission denied, disk full) → red message, exit `1`. The atomic write means partial state is impossible.

## Testing

All tests are unit-level except a single end-to-end run through the existing `test_e2e.py` flow. No live network calls.

### `tests/core/test_config.py` (new)

- Per-key precedence: each key resolves correctly when set only via CLI / only via env / only via file / only via built-in. Mixed sources are honored.
- `KUROI_PROVIDER`, `KUROI_MODEL`, `KUROI_OLLAMA_URL` map to their config keys.
- Validation: unknown provider → `ConfigError`. Ollama with no model → `ConfigError`. Anthropic with no model → falls back to default.
- Malformed TOML → `ConfigError` mentioning the file path.
- Wrong type in TOML → `ConfigError` naming the key and the type.
- File round-trip: `write_config_file()` then `load_config_file()` yields the same values.
- `xdg_config_home()` honors `XDG_CONFIG_HOME` and falls back to `~/.config`.

### `tests/providers/test_factory.py` (new)

- Dispatches to `AnthropicProvider` for `provider="anthropic"`.
- Dispatches to `OllamaProvider` for `provider="ollama"`.
- Raises for an unknown provider name (defense in depth — should already be caught by `resolve_config`).

### `tests/providers/test_ollama.py` (new, mirrors `test_anthropic.py`)

Inject a stub `httpx.Client` via constructor. Cases:

- Happy path: stub returns valid JSON in `message.content`. Findings are parsed. Request URL is `{url}/api/chat`; body includes `format: "json"` and `stream: false`; system + user prompts match expectations; model name is forwarded.
- Empty `llm_category_ids` → returns `[]` without making a request.
- Stub raises `httpx.ConnectError` → `[]`.
- Stub returns 500 → `[]`.
- Stub returns 200 with non-JSON body → `[]`.
- Stub returns 200 with valid JSON but no `message.content` → `[]`.
- Hallucinated indices in payload → dropped (already covered by `parse_findings_payload`; this test asserts wiring).

### `tests/cli/test_setup.py` (new)

Drive via `CliRunner` with scripted `input=...`. Use a `tmp_path` for `XDG_CONFIG_HOME`. Monkeypatch the Ollama probe.

- First-run anthropic: pick provider, pick model from curated list. File contents match expected.
- Re-run shows existing values as defaults — pressing Enter keeps them.
- Ollama probe success: model picker lists stub-returned models; selection writes correct file.
- Ollama probe failure: warning printed, free-text model accepted.
- `ANTHROPIC_API_KEY` unset → warning printed (no failure).
- Atomic write: simulate write failure mid-flow → original file untouched.

### `tests/cli/test_run_provider_wiring.py` (new)

Monkeypatch `make_provider` to return a stub. Assert:

- `--provider ollama --model llama3.1:8b --ollama-url http://example:11434` reaches `resolve_config` correctly.
- With no flags, no env, no file → built-in Anthropic default.

### `tests/cli/test_doctor.py` (extend)

- Doctor prints the resolved provider and model.
- With provider Ollama and an unreachable URL, doctor prints a clear "unreachable" line (no traceback).

### `tests/test_e2e.py` (extend)

- One Ollama-path scenario through the full run/verify/undo flow, using a stub HTTP transport. Mirrors the existing Anthropic e2e structure.

## Out of scope (revisit later if asked)

- Profiles UI.
- A second OpenAI-compatible target (vLLM, LM Studio, llama.cpp). Adding one would justify revisiting the "native HTTP" decision in favor of a small OpenAI-compatible client.
- Provider-specific runtime tunables (temperature, max tokens beyond a sensible constant, system-prompt overrides).
- Saving `ANTHROPIC_API_KEY` to the config file. Secrets stay in env or the user's secret manager.
