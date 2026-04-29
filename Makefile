.PHONY: help test lint format typecheck clean install setup doctor run verify models backups coverage build bump-patch bump-minor bump-major _check-bump _bump-success

PYTHON := python3
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
	@echo "  make install        Install package in editable mode with dev deps"
	@echo ""
	@echo "Release:"
	@echo "  make build          Build sdist and wheel into dist/"
	@echo "  make bump-patch     Bump patch version (0.1.0 -> 0.1.1)"
	@echo "  make bump-minor     Bump minor version (0.1.0 -> 0.2.0)"
	@echo "  make bump-major     Bump major version (0.1.0 -> 1.0.0)"

test:
	@$(PYTHON) -m pytest tests -v

coverage:
	@$(PYTHON) -m pytest --cov=kuroi --cov-report=term-missing tests/

lint:
	@ruff check $(SRC) && ruff format --check $(SRC) && echo "Lint OK"

format:
	@ruff check --fix $(SRC) && ruff format $(SRC) && echo "Format OK"

typecheck:
	@mypy $(SRC) && echo "Typecheck OK"

clean:
	@rm -rf __pycache__ $(SRC)/__pycache__ tests/__pycache__
	@rm -rf $(SRC)/**/__pycache__
	@rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	@rm -rf dist build src/kuroi.egg-info kuroi.egg-info
	@find . -name "*.pyc" -delete
	@echo "Cleaned"

install:
	@$(PYTHON) -m pip install -e ".[dev]"
	@echo "Installed kuroi in editable mode with dev deps"

setup:
	@$(PYTHON) -m kuroi setup

doctor:
	@$(PYTHON) -m kuroi doctor

models:
	@$(PYTHON) -m kuroi models

backups:
	@$(PYTHON) -m kuroi backups

run:
ifndef PDF
	@echo "Usage: make run PDF=<path>"
else
	@$(PYTHON) -m kuroi run $(PDF)
endif

verify:
ifndef PDF
	@echo "Usage: make verify PDF=<path>"
else
	@$(PYTHON) -m kuroi verify $(PDF)
endif

build:
	@$(PYTHON) -c "import build" >/dev/null 2>&1 || { \
		echo "Error: the 'build' package is not installed"; \
		echo ""; \
		echo "Install it with one of:"; \
		echo "  pipx install build"; \
		echo "  pip install --user build"; \
		echo "  uv tool install build"; \
		exit 1; \
	}
	@rm -rf dist build src/kuroi.egg-info kuroi.egg-info
	@$(PYTHON) -m build
	@echo ""
	@echo "Built artifacts:"
	@ls -1 dist

_check-bump:
	@command -v bump-my-version >/dev/null 2>&1 || { \
		echo "Error: bump-my-version is not installed"; \
		echo ""; \
		echo "Install it with one of:"; \
		echo "  pipx install bump-my-version"; \
		echo "  pip install --user bump-my-version"; \
		echo "  uv tool install bump-my-version"; \
		exit 1; \
	}

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

bump-patch: _check-bump
	@bump-my-version bump patch
	@$(MAKE) --no-print-directory _bump-success

bump-minor: _check-bump
	@bump-my-version bump minor
	@$(MAKE) --no-print-directory _bump-success

bump-major: _check-bump
	@bump-my-version bump major
	@$(MAKE) --no-print-directory _bump-success
