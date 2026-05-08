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

### Changed
- The default (non-chunked) run path now retries on hard provider
  failures (was: no retry). Use `--max-retries 0` to opt out.

## [0.1.0] - 2026-04-29

### Added
- Initial release: `kuroi run`, `diff`, `verify`, `undo`, `models`,
  `backups`, `config`, `doctor`, `setup`.
- Anthropic and Ollama LLM providers.
- Built-in PII rule pack (`pii-en.yaml`).
