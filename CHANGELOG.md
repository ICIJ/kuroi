# Changelog

All notable changes to kuroi are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/) and this
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- MkDocs documentation site with Material dark theme, deployed to GitHub Pages.
- Configurable retry policy: `--max-retries`, `--retry-backoff`,
  `--retry-backoff-multiplier` on `kuroi run`; corresponding
  `KUROI_MAX_RETRIES`, `KUROI_RETRY_BACKOFF`,
  `KUROI_RETRY_BACKOFF_MULTIPLIER` env vars; `[retry]` table in
  `config.toml`. Defaults reproduce the historical 2s/4s schedule.
- Automatic subdivision on batch failure. When a batch still fails
  after retries, kuroi now halves it (by pages, then by word range
  with a 50-word overlap) and recurses, instead of aborting the run.
  Single pages too dense for the active model continue to surface a
  clear `BatchError` with diagnostics. New optional `page_word_range`
  field on the `chunk_request` audit record records sub-page slices.
- `--layout-aware` / `--no-layout-aware` flag (and `[prompt] layout_aware`
  config key) wraps the LLM prompt with PyMuPDF block boundaries
  (`<block id="N">…</block>`). Helps the model disambiguate field labels
  from values and reduces context loss across subdivided batches. Off by
  default. Adds ~5% prompt-token overhead on typical pages; the per-batch
  progress line shows `blocks=N` when on so users can measure the cost.

### Changed
- The default (non-chunked) run path now retries on hard provider
  failures (was: no retry). Use `--max-retries 0` to opt out.

### Fixed
- Anthropic responses truncated at `max_tokens` previously emitted a
  chunk record with zero findings — silent data loss. They now signal
  the chunker to subdivide, recovering the lost findings on smaller
  prompts.
- Anthropic `prompt is too long` errors no longer crash the run; they
  are caught and translated into a subdivide signal.
- Ollama malformed-content responses (missing `message.content`,
  non-JSON body despite `format: "json"`, non-object payload)
  previously emitted a chunk record with zero findings. They now
  trigger subdivision instead.

## [0.1.0] - 2026-04-29

### Added
- Initial release: `kuroi run`, `diff`, `verify`, `undo`, `models`,
  `backups`, `config`, `doctor`, `setup`.
- Anthropic and Ollama LLM providers.
- Built-in PII rule pack (`pii-en.yaml`).
