# Docs feature parity update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring kuroi's user-facing docs in sync with shipped features — claude-cli provider, instruction decomposition, per-category model routing, prompt caching, layout-aware mode — and fix a Haiku model-ID/pricing mismatch surfaced during the audit.

**Architecture:** Most reference pages (`docs/reference/cli.md`, `docs/reference/rule-schema.md`) are auto-generated from source by scripts under `docs/_scripts/`; refreshing them via `make docs-gen` closes those gaps. The remaining work is hand edits to user-guide and developer-guide pages, plus a single data fix to `pricing.json` so docs and runtime agree on the Haiku model identifier and price. Verification is `make docs-build` (strict zensical build) plus `git diff` on auto-generated outputs.

**Tech Stack:** Python 3.12 (Typer CLI introspection), zensical (docs site), Make (`docs-gen`, `docs-build`), Markdown.

---

## File map

**Modified (data — affects both runtime and docs accuracy):**
- `src/kuroi/data/pricing.json` — rename `claude-haiku-4-5` key to `claude-haiku-4-5-20251001` so it matches `setup.py` and the docs.

**Modified (auto-generated, refreshed by `make docs-gen`):**
- `docs/reference/cli.md` — picks up `--claude-cli-path`, `--claude-cli-timeout`, and `claude-cli` in `--provider` from the live Typer app.
- `docs/reference/rule-schema.md` — picks up `model` field on `Category`.

**Modified (hand-edited):**
- `docs/index.md` — fix backup path (`~/.local/state/` → `~/.local/share/`).
- `docs/user-guide/providers.md` — sync model prices with `pricing.json`, fix "Two providers" stale claim, lift per-rule `model:` override out of one provider tab into a generic section.
- `docs/user-guide/quickstart.md` — mention claude-cli + ollama as alternatives in step 1, plug `kuroi setup`.
- `docs/user-guide/install.md` — list claude-cli subscription as a third LLM-provider option in system requirements.
- `docs/user-guide/troubleshooting.md` — add Claude CLI section (binary not found, `ANTHROPIC_API_KEY` shadowing, login).
- `docs/user-guide/prompt-tuning.md` — add an "Instruction decomposition (Ollama)" section.
- `docs/user-guide/audit-and-undo.md` — explain the `cache_creation_input_tokens` / `cache_read_input_tokens` fields.
- `docs/developer-guide/adding-a-provider.md` — update `Provider` protocol skeleton (full current signature), `ChunkRecord` example (cache fields), pricing reference (json not py), and docs build command.
- `docs/developer-guide/writing-rule-packs.md` — document the optional per-category `model` field.

**Created:** none (all changes go to existing files; the plan itself lives at `docs/superpowers/plans/2026-05-09-docs-feature-parity.md` and is excluded from the zensical site by `exclude_docs`).

---

## Task 1: Fix Haiku model ID & price in `pricing.json`

**Why first:** Subsequent doc edits cite prices from `kuroi models` output. Fixing the data first means later tasks can copy real `kuroi models` output instead of guessing.

**Files:**
- Modify: `src/kuroi/data/pricing.json`
- Reference: `src/kuroi/cli/setup.py:28` (uses `claude-haiku-4-5-20251001`)
- Reference: `docs/user-guide/providers.md:22` (uses `claude-haiku-4-5-20251001` and `$1.00 / $5.00`)

**Decision context (already made — do not re-litigate):**
- Canonical Haiku model ID is `claude-haiku-4-5-20251001` (Anthropic's release-dated form, what `setup.py` registers and what providers.md displays).
- The `claude-haiku-4-5` key in `pricing.json` is the bug — it never matches a real call's model field.
- The published rates (per Anthropic) are `$1.00 / $5.00` per Mtok, not the `$0.80 / $4.00` currently in `pricing.json`. Update both fields to align with what providers.md already shows.

- [ ] **Step 1: Read the current pricing.json line**

```bash
grep -n "haiku" src/kuroi/data/pricing.json
```

Expected output:

```
8:      "claude-haiku-4-5":  {"input_per_million":  0.80, "output_per_million":  4.00, "cache_write_multiplier": 1.25, "cache_read_multiplier": 0.1}
```

- [ ] **Step 2: Update the pricing entry**

Edit `src/kuroi/data/pricing.json` line 8 — change the key and the two rate fields:

```json
"claude-haiku-4-5-20251001":  {"input_per_million":  1.00, "output_per_million":  5.00, "cache_write_multiplier": 1.25, "cache_read_multiplier": 0.1}
```

- [ ] **Step 3: Verify `kuroi models` output matches docs**

```bash
uv run kuroi models | grep -i haiku
```

Expected (substring match): `claude-haiku-4-5-20251001` and `$1.00 / $5.00 per Mtok`.

If it doesn't match, the canonical-name decision was wrong — stop and surface the discrepancy rather than papering over it.

- [ ] **Step 4: Run unit tests for pricing**

```bash
uv run pytest tests -k "pricing" -v
```

Expected: PASS. If a test pinned the old key/price, update the test fixture to the new values (the fix is a data correction, not a behavior change).

- [ ] **Step 5: Commit**

```bash
git add src/kuroi/data/pricing.json
# plus any updated test fixture
git commit -m "fix(pricing): align claude-haiku-4-5 key and rates with shipped model id"
```

---

## Task 2: Refresh auto-generated reference pages

**Files:**
- Regenerate (do not hand-edit): `docs/reference/cli.md`
- Regenerate (do not hand-edit): `docs/reference/rule-schema.md`
- Driver: `docs/_scripts/gen_cli_reference.py`, `docs/_scripts/gen_rule_schema.py`
- Make target: `make docs-gen` (in `Makefile`)

- [ ] **Step 1: Confirm the gaps before running the generator**

```bash
grep -n "claude-cli\|claude_cli" docs/reference/cli.md ; echo "---"
grep -n "model" docs/reference/rule-schema.md
```

Expected before regen:
- First grep returns nothing (no `claude-cli` mentions).
- Second grep does not list a `model` field row in the Category table.

- [ ] **Step 2: Regenerate**

```bash
make docs-gen
```

Expected stdout:

```
Wrote docs/reference/cli.md
Wrote docs/reference/rule-schema.md
```

- [ ] **Step 3: Verify the CLI reference picked up new flags**

```bash
grep -n "claude-cli-path\|claude-cli-timeout\|claude-cli" docs/reference/cli.md
```

Expected: at least three matches — `--claude-cli-path` flag, `--claude-cli-timeout` flag, and `claude-cli` mentioned as a `--provider` option.

If anything is missing, the Typer help string in `src/kuroi/cli/run.py` for that flag is wrong (or the flag is hidden) — investigate at the source, do not edit the generated file.

- [ ] **Step 4: Verify the rule schema picked up the `model` field**

```bash
grep -n "^| \`model\`" docs/reference/rule-schema.md
```

Expected: one match in the Category section showing `| \`model\` | \`str | None\` | … |`.

- [ ] **Step 5: Commit**

```bash
git add docs/reference/cli.md docs/reference/rule-schema.md
git commit -m "docs: regenerate CLI and rule-schema references"
```

---

## Task 3: Fix backup path in `docs/index.md`

**Files:**
- Modify: `docs/index.md:24`

- [ ] **Step 1: Read the offending line**

```bash
sed -n '17,26p' docs/index.md
```

Expected output (around line 24): `✓ Backup at: ~/.local/state/kuroi/backups/report-20260430-093102.pdf`

- [ ] **Step 2: Replace `state` with `share`**

In `docs/index.md`, change:

```
✓ Backup at: ~/.local/state/kuroi/backups/report-20260430-093102.pdf
```

to:

```
✓ Backup at: ~/.local/share/kuroi/backups/report-20260430-093102.pdf
```

(The default backup dir is under `$XDG_DATA_HOME` which resolves to `~/.local/share/`, matching `quickstart.md:31` and `troubleshooting.md:92`.)

- [ ] **Step 3: Verify no remaining `~/.local/state` references**

```bash
grep -rn "~/.local/state/kuroi" docs/
```

Expected: no output (zero hits).

- [ ] **Step 4: Commit**

```bash
git add docs/index.md
git commit -m "docs: correct backup path on landing page (state → share)"
```

---

## Task 4: Update `docs/user-guide/providers.md`

**Files:**
- Modify: `docs/user-guide/providers.md` (multiple hunks)

This task has three concrete edits. Apply each, then commit as one logical change.

### Edit 4a: Fix the "Two providers" claim

- [ ] **Step 1: Replace the lead paragraph (line 4)**

Change:

```markdown
plain regex misses. Two providers ship with kuroi.
```

to:

```markdown
plain regex misses. Three providers ship with kuroi: Anthropic (cloud, per-token), Claude CLI (cloud, subscription), and Ollama (local).
```

### Edit 4b: Sync `kuroi models` example with actual output

The example block at lines 16–38 is hand-written but should match what `kuroi models` actually prints after Task 1.

- [ ] **Step 2: Capture real output**

```bash
uv run kuroi models > /tmp/kuroi-models-actual.txt
cat /tmp/kuroi-models-actual.txt
```

- [ ] **Step 3: Replace the fenced block at `providers.md:16-38`**

Replace the existing `$ kuroi models` fenced block with the contents of `/tmp/kuroi-models-actual.txt` verbatim (preserve the leading `$ kuroi models` and surrounding fence). The Haiku line should now read `claude-haiku-4-5-20251001 $1.00 / $5.00 per Mtok`. If the per-rule example at line 104 references the same model ID, it stays unchanged (already `claude-haiku-4-5-20251001`).

### Edit 4c: Document `--claude-cli-path` and `--claude-cli-timeout`, lift per-rule override to a shared section

The current text only mentions per-rule `model:` overrides inside the Claude CLI tab (lines 97–105) but the feature works for all three providers (per `Provider` protocol's `model` parameter). Move the explanation to a new top-level subsection so readers don't think it's claude-cli-specific.

- [ ] **Step 4: Inside the `=== "Claude CLI"` tab (around line 72), append CLI-flag examples after the persistent-config block**

After the existing `claude_cli` TOML block (line 85), add:

````markdown

    Or pass overrides per invocation without editing the config:

    ```sh
    $ kuroi run document.pdf --provider claude-cli \
        --claude-cli-path /opt/claude/bin/claude \
        --claude-cli-timeout 600
    ```

    `--claude-cli-path` overrides the bundled `claude-agent-sdk` binary (point
    it at a system-installed `claude` if you prefer). `--claude-cli-timeout`
    is the per-call timeout in seconds (default `300`); raise it for long
    documents that exceed the default budget.
````

- [ ] **Step 5: Replace the per-rule override block (current lines 97–105) inside the Claude CLI tab with a one-line cross-link**

Inside the Claude CLI tab, change the "Per-rule `model:` overrides…" paragraph and YAML example to:

```markdown
    Per-rule `model:` overrides work for every provider — see
    [Per-category model routing](#per-category-model-routing) below.
```

- [ ] **Step 6: Add a new `## Per-category model routing` section after the Configure-a-provider section, before `## Configuration precedence`**

Insert before the existing `## Configuration precedence` heading (currently line 130):

````markdown
## Per-category model routing

Every category in a rule pack accepts an optional `model:` field. When set,
calls for that category are dispatched against the named model instead of
the provider's default — useful for routing cheap, repetitive categories
(e.g. emails) to a small fast model and reserving the heavy default model
for harder ones (e.g. names, addresses).

```yaml
- id: contact_info
  llm: true
  model: claude-haiku-4-5-20251001

- id: full_name
  llm: true
  # falls back to the configured default model
```

The chunker groups dispatches by `model` and runs each group concurrently
per batch, so per-category routing does not serialize calls. The override
applies regardless of whether the provider is `anthropic`, `claude-cli`, or
`ollama` — pick a model id the chosen provider can serve.
````

- [ ] **Step 7: Build the docs and verify**

```bash
make docs-build
```

Expected: `Docs build OK (site/)`. If strict mode flags a broken link, the most likely culprit is the new `#per-category-model-routing` anchor — confirm the heading text matches.

- [ ] **Step 8: Commit**

```bash
git add docs/user-guide/providers.md
git commit -m "docs(providers): three providers, sync prices, surface model routing"
```

---

## Task 5: Update `docs/user-guide/quickstart.md`

**Files:**
- Modify: `docs/user-guide/quickstart.md` (steps 1 + cost-line consistency)

- [ ] **Step 1: Replace section "1. Set your provider key" (lines 5–15)**

Replace the existing section body with:

```markdown
## 1. Pick a provider

kuroi supports three LLM providers — pick one:

- **Anthropic API** (cloud, default). Set `ANTHROPIC_API_KEY` once per shell:
  ```sh
  export ANTHROPIC_API_KEY=sk-ant-...
  ```
- **Claude CLI** (cloud, subscription). If you have a Claude Code plan, no
  API key is needed. Run `claude /login` once, then pass `--provider claude-cli`.
- **Ollama** (local, offline). Install [Ollama](https://ollama.ai) and pull a
  model — no API key, no outbound HTTP. Pass `--provider ollama --model <name>`.

Run `kuroi setup` to configure interactively, or jump to
[LLM providers](providers.md) for full details on each.
```

- [ ] **Step 2: Make the cost-estimate example provider-aware**

The example output at line 27 shows `anthropic/claude-opus-4-7`. Leave it as-is for the default-path narrative — but immediately after the fenced block (around line 33), insert:

```markdown
The provider/model in the cost line reflects whatever is configured.
For Claude CLI runs the cost shows as `subscription`; for Ollama it shows
as `local (free)`.
```

- [ ] **Step 3: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK`.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide/quickstart.md
git commit -m "docs(quickstart): introduce all three providers up front"
```

---

## Task 6: Update `docs/user-guide/install.md`

**Files:**
- Modify: `docs/user-guide/install.md:31-37` (System requirements)

- [ ] **Step 1: Replace the "An LLM provider" bullet at line 34**

Replace:

```markdown
- **An LLM provider.** Either an Anthropic API key (cloud) or a running
  [Ollama](https://ollama.ai) instance (local). See [LLM providers](providers.md).
```

with:

```markdown
- **An LLM provider.** One of:
    - An Anthropic API key (cloud, per-token).
    - A Claude Code subscription (cloud, flat-rate). Install
      `claude-agent-sdk` (`pip install claude-agent-sdk`) or the
      `@anthropic-ai/claude-code` npm package and run `claude /login` once.
    - A running [Ollama](https://ollama.ai) instance (local, free).

    See [LLM providers](providers.md) for setup details.
```

- [ ] **Step 2: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK`.

- [ ] **Step 3: Commit**

```bash
git add docs/user-guide/install.md
git commit -m "docs(install): list claude-cli subscription as a provider option"
```

---

## Task 7: Update `docs/user-guide/troubleshooting.md`

**Files:**
- Modify: `docs/user-guide/troubleshooting.md` (extend "ANTHROPIC_API_KEY not set" + add a new "Claude CLI errors" section)

- [ ] **Step 1: Extend the "ANTHROPIC_API_KEY not set" section (lines 25–32)**

Replace lines 25–32 with:

```markdown
## "ANTHROPIC_API_KEY not set"

```sh
$ export ANTHROPIC_API_KEY=sk-ant-...
```

Persist by adding the export to your shell rc file. Or switch providers:

- **Claude CLI** (no API key, uses your Claude Code subscription) — see
  [Claude CLI errors](#claude-cli-errors) below for setup quirks.
- **Ollama** (local, offline) — see [LLM providers](providers.md).
```

- [ ] **Step 2: Add a new "Claude CLI errors" section after the existing "Provider rate limits" section (around line 56)**

Insert before the `## "PDF is too large to extract"` heading:

````markdown
## Claude CLI errors

The `claude-cli` provider shells out to the `claude` binary (bundled by
`claude-agent-sdk`, or the `@anthropic-ai/claude-code` npm install). Three
failure modes are common:

### "claude binary not found"

```sh
$ kuroi run report.pdf --provider claude-cli
ConfigError: claude CLI not found
```

The bundled binary is missing or outside `PATH`. Either:

```sh
$ pip install --upgrade claude-agent-sdk
# or:
$ npm install -g @anthropic-ai/claude-code
```

If you have a system install elsewhere, point at it:

```sh
$ kuroi run report.pdf --provider claude-cli \
    --claude-cli-path /opt/claude/bin/claude
```

### "Not authenticated" / login required

Run the one-time login flow:

```sh
$ claude /login
```

This stores OAuth credentials in your home directory. kuroi never sees
the credential — it only invokes the binary.

### `ANTHROPIC_API_KEY` shadowing your subscription

If you set `ANTHROPIC_API_KEY` and select `--provider claude-cli`, the
`claude` binary will silently prefer per-token API billing over your
subscription. kuroi prints a warning at startup:

```
warning: ANTHROPIC_API_KEY is set; claude-cli will bill against the API
key, not your subscription. Unset the variable to force subscription
billing.
```

Unset the variable in the calling shell to force subscription billing:

```sh
$ unset ANTHROPIC_API_KEY
$ kuroi run report.pdf --provider claude-cli
```

### Per-call timeout

Long documents may exceed the default 300-second per-call budget. Raise
it with `--claude-cli-timeout`, or persist via:

```toml
[claude_cli]
timeout_s = 600
```
````

- [ ] **Step 3: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK` and the new anchors (`#claude-cli-errors`) reachable.

- [ ] **Step 4: Verify the cross-link from step 1 resolves**

```bash
grep -n "claude-cli-errors" docs/user-guide/troubleshooting.md
```

Expected: at least one anchor link reference and one heading.

- [ ] **Step 5: Commit**

```bash
git add docs/user-guide/troubleshooting.md
git commit -m "docs(troubleshooting): add claude-cli failure modes"
```

---

## Task 8: Document instruction decomposition in `docs/user-guide/prompt-tuning.md`

**Why prompt-tuning.md (not a new page):** Decomposition is one prompt-construction lever among others (layout-aware, batching). Keeping all of them in one place lowers reader friction.

**Files:**
- Modify: `docs/user-guide/prompt-tuning.md` (append a new section)

- [ ] **Step 1: Find an insertion point**

```bash
grep -n "^## " docs/user-guide/prompt-tuning.md
```

Read the headings — pick the position after the layout-aware section and before any "Next steps" / "Further reading" tail.

- [ ] **Step 2: Append a new "Instruction decomposition (Ollama)" section**

Insert before the file's closing "Next steps" / "Further reading" section (or at the end if there isn't one):

````markdown
## Instruction decomposition (Ollama)

Small local models (Ollama) handle one redaction rule per prompt reliably
but choke on multi-rule `--instruct` strings. kuroi automatically
decomposes a multi-rule instruction into atomic sub-rules and dispatches
one provider call per rule per batch when the configured provider is
`ollama`. The Anthropic and Claude CLI providers receive the original
instruction unchanged — they handle multi-rule prompts natively.

```sh
$ kuroi run report.pdf --provider ollama --model llama3.1:8b -i \
  '1. Redact every email address.
   2. Redact phone numbers in any format.
   3. Redact full personal names.'
```

What kuroi does, in order:

1. **Deterministic split** — `parse_instruction()` looks for numbering
   (`1.`, `2.`), bullets (`-`, `*`), or blank-line-separated paragraphs
   and returns one rule per detected unit. The example above splits into
   three rules without an LLM call.
2. **LLM fallback** — when the deterministic splitter returns one rule
   but the input is long enough to plausibly contain several, kuroi
   makes a single "split this" call against the same Ollama provider.
   Best-effort: any failure collapses back to the original instruction
   so runs never regress.
3. **Per-rule dispatch** — each sub-rule is sent as its own
   single-instruction prompt to the model, in parallel within each
   batch.

The audit log records the decomposition: look for `instruction_decompose`
events in the JSONL, with the parsed sub-rules and the strategy
(`deterministic` or `llm_fallback`).

To opt out, send a single-rule instruction or use Anthropic / Claude CLI.
````

- [ ] **Step 3: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK`.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide/prompt-tuning.md
git commit -m "docs(prompt-tuning): document Ollama instruction decomposition"
```

---

## Task 9: Update `docs/user-guide/audit-and-undo.md` with cache-token explanation

**Files:**
- Modify: `docs/user-guide/audit-and-undo.md` (extend the audit-record fields list)

- [ ] **Step 1: Locate the section that documents `ChunkRecord` fields**

```bash
grep -n "tokens_in\|ChunkRecord\|chunk_idx" docs/user-guide/audit-and-undo.md
```

If a fields section already exists, append to its list. If not, add a new subsection after the existing audit-format introduction.

- [ ] **Step 2: Add the cache-token fields**

Append (where the per-chunk fields are listed):

```markdown
- `cache_creation_input_tokens` — input tokens written to Anthropic's
  prompt cache on this call. These are billed at the cache-write rate
  (1.25× input by default) but mean subsequent matching prompts pay the
  cheaper read rate.
- `cache_read_input_tokens` — input tokens served from cache on this
  call. These are billed at the cache-read rate (0.1× input by default).
  A growing share of cache reads across a run is a sign caching is
  working — it should reduce total cost on repetitive batches.

Cache fields are zero for providers that do not support prompt caching
(Ollama, Claude CLI). They are populated by the Anthropic provider when
the request structure permits caching.
```

- [ ] **Step 3: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK`.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide/audit-and-undo.md
git commit -m "docs(audit): explain cache_creation/cache_read token fields"
```

---

## Task 10: Update `docs/developer-guide/adding-a-provider.md`

**Files:**
- Modify: `docs/developer-guide/adding-a-provider.md` (Protocol skeleton + ChunkRecord example + factory + Step 3 pricing reference + Step 5 build command)

- [ ] **Step 1: Replace the Protocol block (lines 9–21) with the current full signature**

Replace:

```python
class Provider(Protocol):
    name: str   # e.g. "anthropic"
    model: str  # e.g. "claude-opus-4-7"

    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        seed: int | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]: ...
```

with:

```python
class Provider(Protocol):
    name: str   # e.g. "anthropic"
    model: str  # e.g. "claude-opus-4-7"

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

And update the prose at lines 23–25 to reflect the extra parameters:

```markdown
Two attributes for identification, one method that takes word-indexed
pages and returns a flat list of `Finding`s plus per-chunk audit
records. Three keyword-only parameters are advisory — providers may
ignore them when their backend doesn't support the feature:

- `instructions` — natural-language redaction instructions from the user
  (`-i`/`--instruct`). Append to the prompt; on Ollama, the chunker has
  already decomposed multi-rule instructions into atomic sub-rules.
- `attempt` — zero-based retry index. Use it to scale per-call budgets
  (e.g. Ollama gives slow models more time on each retry).
- `layout_aware` — when `True`, wrap the prompt with PyMuPDF block
  boundaries so the model sees paragraph structure.
- `model` — per-call override for `self.model`, used by the chunker
  when a category in the rule pack carries its own `model:` field.
```

- [ ] **Step 2: Update the skeleton's `detect_redactions` signature (lines 81–87)**

Replace:

```python
    def detect_redactions(
        self,
        pages: tuple[Page, ...],
        llm_category_ids: tuple[str, ...],
        *,
        seed: int | None = None,
    ) -> tuple[list[Finding], list[ChunkRecord]]:
```

with:

```python
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
    ) -> tuple[list[Finding], list[ChunkRecord]]:
```

- [ ] **Step 3: Update the `ChunkRecord` construction (lines 99–111)**

Replace the existing `ChunkRecord(...)` block with:

```python
        chunk = ChunkRecord(
            chunk_idx=0,
            pages=tuple(p.number for p in pages),
            temperature=0.0,
            seed_requested=seed,
            seed_honored=seed is not None,  # True if your API honored it
            system_fingerprint=None,         # or whatever the API exposes
            prompt_sha256=prompt_sha,
            response_sha256=response_sha,
            tokens_in=...,
            tokens_out=...,
            cache_creation_input_tokens=0,   # populate if your provider returns
            cache_read_input_tokens=0,       # cache-write/read counters
            duration_ms=duration_ms,
        )
```

- [ ] **Step 4: Update the factory dispatch example (lines 134–142)**

Replace:

```python
def make_provider(config: Config) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(model=config.model)
    if config.provider == "ollama":
        return OllamaProvider(model=config.model, url=config.ollama_url)
    if config.provider == "myprovider":
        return MyProvider(model=config.model)
    raise ValueError(f"Unknown provider: {config.provider!r}")
```

with:

```python
def make_provider(config: Config) -> Provider:
    if config.provider == "anthropic":
        return AnthropicProvider(model=config.model)
    if config.provider == "claude-cli":
        return ClaudeCliProvider(
            model=config.model,
            cli_path=config.claude_cli_path,
            timeout_s=config.claude_cli_timeout_s,
        )
    if config.provider == "ollama":
        return OllamaProvider(model=config.model, url=config.ollama_url)
    if config.provider == "myprovider":
        return MyProvider(model=config.model)
    raise ValueError(f"Unknown provider: {config.provider!r}")
```

- [ ] **Step 5: Update the `ProviderName` literal example (lines 148–151)**

Replace:

```python
ProviderName = Literal["anthropic", "ollama", "myprovider"]
VALID_PROVIDERS: tuple[ProviderName, ...] = (
    "anthropic", "ollama", "myprovider",
)
```

with:

```python
ProviderName = Literal["anthropic", "claude-cli", "ollama", "myprovider"]
VALID_PROVIDERS: tuple[ProviderName, ...] = (
    "anthropic", "claude-cli", "ollama", "myprovider",
)
```

- [ ] **Step 6: Fix Step 3 pricing reference (lines 159–166)**

The current text says `core/pricing.py`, but pricing data lives in `data/pricing.json`. Replace lines 159–166:

```markdown
## Step 3: Add pricing

If your provider charges per token, add an entry to
`src/kuroi/data/pricing.json` keyed by the model id (the same string
returned by `kuroi models`). The required shape is:

```json
"my-model-id": {
  "input_per_million":  1.00,
  "output_per_million": 5.00,
  "cache_write_multiplier": 1.25,
  "cache_read_multiplier": 0.1
}
```

Free / local providers can leave the file unchanged — `estimate_cost`
already handles missing rates by returning `$0.00`. To refresh a cached
copy, ship a new `pricing.json` and run
`kuroi config refresh-pricing --from path/to/pricing.json`.
```

- [ ] **Step 7: Fix Step 5 build command (lines 188–190)**

Replace:

```markdown
The CLI reference and Python API reference update automatically when
you re-run `uv run mkdocs build --strict`.
```

with:

```markdown
The CLI reference and rule-schema reference update automatically when
you re-run `make docs-gen` (which regenerates them from the live Typer
app and dataclasses). `make docs-build` then verifies the strict
zensical build.
```

- [ ] **Step 8: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK`.

- [ ] **Step 9: Commit**

```bash
git add docs/developer-guide/adding-a-provider.md
git commit -m "docs(adding-a-provider): sync skeleton with current Provider protocol"
```

---

## Task 11: Update `docs/developer-guide/writing-rule-packs.md`

**Files:**
- Modify: `docs/developer-guide/writing-rule-packs.md` (add per-category model section)

- [ ] **Step 1: Locate the Category-fields section**

```bash
grep -n "^## \|Category(" docs/developer-guide/writing-rule-packs.md
```

Pick the section that documents Category fields (likely near the YAML examples).

- [ ] **Step 2: Append a new "Per-category model routing" subsection**

Add before the doc's "Next steps" tail (or at the end if none):

````markdown
## Per-category model routing

Each category accepts an optional `model:` field. When present, the
chunker dispatches that category's calls against the named model instead
of the default model selected at the top level. The chunker groups by
model and runs each group concurrently per batch, so adding `model:`
to a few categories does not serialize the run.

```yaml
categories:
  - id: emails
    label: Email addresses
    detection: llm
    confidence: high
    model: claude-haiku-4-5-20251001     # cheap, plenty for emails

  - id: full_names
    label: Personal names
    detection: llm
    confidence: high
    # no model: → uses the run's default model
```

The override applies whatever provider you choose; pass a model id the
configured provider can serve. See
[LLM providers → Per-category model routing](../user-guide/providers.md#per-category-model-routing)
for an end-user-facing summary.
````

- [ ] **Step 3: Build and verify**

```bash
make docs-build
```

Expected: `Docs build OK`. The cross-link to `providers.md#per-category-model-routing` requires Task 4 to have landed.

- [ ] **Step 4: Commit**

```bash
git add docs/developer-guide/writing-rule-packs.md
git commit -m "docs(rule-packs): document per-category model routing"
```

---

## Task 12: Final strict build + cross-link sweep

**Why last:** Catches any link, anchor, or auto-gen breakage introduced by earlier tasks. Strict zensical build is what CI runs.

- [ ] **Step 1: Run the strict build**

```bash
make docs-build
```

Expected: `Docs build OK (site/)`.

- [ ] **Step 2: Verify auto-gen pages are still in sync (CI parity)**

```bash
make docs-gen
git status --short docs/reference/
```

Expected: no modifications. If anything shows up, commit it as a separate `docs: regenerate` commit before the next CI run.

- [ ] **Step 3: Cross-link sweep — every claude-cli mention reachable**

```bash
grep -rn "claude-cli\|claude_cli\|Claude CLI" docs/ | grep -v "superpowers/" | wc -l
```

Expected: a non-trivial count across providers.md, troubleshooting.md, install.md, quickstart.md, adding-a-provider.md, and the regenerated cli.md / rule-schema.md isn't expected to mention it but config.md should.

- [ ] **Step 4: Spot-check a handful of generated anchors**

```bash
grep -n "per-category-model-routing\|claude-cli-errors" docs/user-guide/providers.md docs/user-guide/troubleshooting.md docs/developer-guide/writing-rule-packs.md
```

Expected: each anchor is defined in one file and referenced from at least one other.

- [ ] **Step 5: If everything is clean, no commit needed; otherwise commit fixes and re-run Step 1.**

---

## Self-review notes

- **Spec coverage:** Every gap surfaced in the audit (haiku ID/price, claude-cli flags in CLI ref, Category model field in rule-schema, providers.md "Two providers" + per-rule override placement, quickstart only-Anthropic, install missing claude-cli, troubleshooting missing claude-cli, audit cache fields undocumented, adding-a-provider stale, writing-rule-packs missing model, index.md backup path, instruction decomposition undocumented) maps to a task. The instruction-decomposition piece lands in prompt-tuning rather than a new page (lower reader friction; flagged in the brainstorm).
- **Placeholder scan:** No "TBD" / "appropriate error handling" / "similar to Task N" — every edit shows the literal markdown to insert.
- **Type / name consistency:** `Provider.detect_redactions` signature shown in Task 10 matches `src/kuroi/providers/base.py:18-29` exactly. `ChunkRecord` cache-field names (`cache_creation_input_tokens`, `cache_read_input_tokens`) match `src/kuroi/core/audit_records.py:31-32`. The Haiku id `claude-haiku-4-5-20251001` is consistent across Tasks 1, 4, 11, and the docs they touch.

## Risk notes for the executor

- Task 1 changes runtime data, not just docs. If a test pinned the old key/price, fix the test alongside. Do not skip the test run.
- Task 2's `make docs-gen` is a no-op for `docs/reference/cli.md` if any flag's Typer help string is empty — if a regen still doesn't surface `--claude-cli-path`, fix the help text in `src/kuroi/cli/run.py` rather than hand-editing the generated file.
- Tasks 4 and 11 cross-link to a new anchor (`#per-category-model-routing`). If you reorder tasks, land Task 4 before Task 11 or strict build will break.
