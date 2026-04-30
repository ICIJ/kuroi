.PHONY: help test lint format typecheck clean install setup doctor run verify models backups coverage build bump-patch bump-minor bump-major _check-uv _bump-success docs docs-build

SRC := src/kuroi

help:
	@echo "kuroi - Strip sensitive data from PDFs with LLM assistance"
	@echo ""
	@echo "Usage:"
	@echo "  make setup          Interactively configure kuroi"
	@echo "  make doctor         Check that everything is working"
	@echo "  make run PDF=x      Redact a PDF"
	@echo "  make verify PDF=x   Check a redacted PDF for leaks"
	@echo "  make models         List available LLM providers and models"
	@echo "  make backups        Manage backups"
	@echo ""
	@echo "Development:"
	@echo "  make test           Run unit tests"
	@echo "  make coverage       Run tests with coverage report"
	@echo "  make lint           Check code style"
	@echo "  make format         Auto-format code"
	@echo "  make typecheck      Run mypy"
	@echo "  make clean          Remove cache files"
	@echo "  make install        Sync the uv environment with dev + docs extras"
	@echo "  make docs           Serve the documentation site locally"
	@echo "  make docs-build     Build docs in --strict mode (matches CI)"
	@echo ""
	@echo "Release:"
	@echo "  make build          Build sdist and wheel into dist/"
	@echo "  make bump-patch     Bump patch version (0.1.0 -> 0.1.1)"
	@echo "  make bump-minor     Bump minor version (0.1.0 -> 0.2.0)"
	@echo "  make bump-major     Bump major version (0.1.0 -> 1.0.0)"

_check-uv:
	@command -v uv >/dev/null 2>&1 || { \
		echo "Error: uv is not installed"; \
		echo ""; \
		echo "Install it from https://docs.astral.sh/uv/getting-started/installation/"; \
		exit 1; \
	}

test: _check-uv
	@uv run pytest tests -v

coverage: _check-uv
	@uv run pytest --cov=kuroi --cov-report=term-missing tests/

lint: _check-uv
	@uv run ruff check $(SRC) && uv run ruff format --check $(SRC) && echo "Lint OK"

format: _check-uv
	@uv run ruff check --fix $(SRC) && uv run ruff format $(SRC) && echo "Format OK"

typecheck: _check-uv
	@uv run mypy $(SRC) && echo "Typecheck OK"

clean:
	@rm -rf __pycache__ $(SRC)/__pycache__ tests/__pycache__
	@rm -rf $(SRC)/**/__pycache__
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	@rm -rf dist build src/kuroi.egg-info kuroi.egg-info
	@find . -name "*.pyc" -delete
	@echo "Cleaned"

install: _check-uv
	@uv sync --extra dev --extra docs
	@echo "Synced kuroi environment with dev + docs extras"

setup: _check-uv
	@uv run kuroi setup

doctor: _check-uv
	@uv run kuroi doctor

models: _check-uv
	@uv run kuroi models

backups: _check-uv
	@uv run kuroi backups

run: _check-uv
ifndef PDF
	@echo "Usage: make run PDF=<path>"
else
	@uv run kuroi run $(PDF)
endif

verify: _check-uv
ifndef PDF
	@echo "Usage: make verify PDF=<path>"
else
	@uv run kuroi verify $(PDF)
endif

build: _check-uv
	@rm -rf dist build src/kuroi.egg-info kuroi.egg-info
	@uv build
	@echo ""
	@echo "Built artifacts:"
	@ls -1 dist

_bump-success:
	@NEW_TAG=$$(git describe --tags --abbrev=0); \
	echo ""; \
	echo "✓ Version bumped to $$NEW_TAG"; \
	echo ""; \
	echo "Next steps:"; \
	echo "  1. Push the commit and tag:"; \
	echo "       git push --follow-tags"; \
	echo "  2. Create a GitHub release for $$NEW_TAG:"; \
	echo "       gh release create $$NEW_TAG --generate-notes"

bump-patch: _check-uv
	@uvx bump-my-version bump patch
	@$(MAKE) --no-print-directory _bump-success

bump-minor: _check-uv
	@uvx bump-my-version bump minor
	@$(MAKE) --no-print-directory _bump-success

bump-major: _check-uv
	@uvx bump-my-version bump major
	@$(MAKE) --no-print-directory _bump-success

docs: _check-uv
	@uv run mkdocs serve -a 0.0.0.0:4000

docs-build: _check-uv
	@uv run mkdocs build --strict
	@echo "Docs build OK (site/)"
