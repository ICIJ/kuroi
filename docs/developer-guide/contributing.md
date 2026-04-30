# Contributing

Welcome. kuroi is small and focused — your first PR can be merged in a day.

## Dev setup

```sh
$ git clone https://github.com/ICIJ/kuroi.git
$ cd kuroi
$ make install                  # editable install with dev extras
$ pip install -e ".[docs]"      # add docs extras if you'll touch the site
$ make doctor
```

`make install` installs the `dev` extras (pytest, ruff, mypy). The `docs`
extras (mkdocs-material, mkdocstrings, mkdocs-typer2, etc.) are declared
separately in `pyproject.toml`; install them with `pip install -e ".[docs]"`
when you need to build the documentation site locally.

## Common Makefile targets

| Target           | What it does                                         |
| ---------------- | ---------------------------------------------------- |
| `make test`      | Run the pytest suite.                                |
| `make coverage`  | Run pytest with a coverage report.                   |
| `make lint`      | `ruff check` + `ruff format --check`.                |
| `make format`    | Auto-format with ruff.                               |
| `make typecheck` | Run mypy in strict mode.                             |
| `make doctor`    | Run `kuroi doctor` to validate your environment.     |
| `make docs`      | Serve the documentation site locally.                |
| `make docs-build`| Build docs in `--strict` mode (matches CI).          |
| `make clean`     | Remove caches and build artifacts.                   |
| `make build`     | Build sdist and wheel into `dist/`.                  |

CI runs `lint`, `typecheck`, `test`, and a strict docs build on every PR.

## Running the docs site

```sh
$ make docs
INFO    -  Serving on http://0.0.0.0:4000/
```

The site live-reloads on every file save. Before opening a PR that
touches docs, run the strict build — that's what CI runs, and it fails
on any unresolved link or missing nav entry:

```sh
$ make docs-build
```

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/).
Common types:

- `feat:` — new user-visible feature.
- `fix:` — bug fix.
- `docs:` — docs changes.
- `refactor:` — internal change with no user-visible effect.
- `test:` — test-only changes.
- `chore:` / `build:` / `ci:` — tooling, deps, workflows.

Subject line ≤ 72 characters, lowercase. No body required for small
changes.

## How the auto-generated reference works

Three reference areas are intended to be generated from code rather than
hand-edited:

- **CLI reference** (`docs/reference/cli.md`) — to be rendered by
  `mkdocs-typer2` from the live `kuroi.cli:app`. Adding or renaming a
  Typer command will then update the docs automatically.
- **Python API reference** (`docs/reference/api/*.md`) — to be rendered
  by `mkdocstrings` from the docstrings on the public modules.
  **Docstrings on the listed modules are part of the public contract.**
- **Rule schema reference** (`docs/reference/rule-schema.md`) — to be
  generated from the `Category` and `RuleSet` dataclasses in
  `kuroi.core.rules`.

Both `mkdocs-typer2` and `mkdocstrings` are listed in the `docs` extras
in `pyproject.toml`.

If your PR removes or renames a public Typer command, public function,
or rule-schema field, run `make docs-build` locally —
`--strict` will fail if any hand-written prose page links to a symbol
that no longer exists. Update the cross-references in the same PR.

## One-time setup notes

- **GitHub Pages source.** On first deploy, a maintainer must set the
  Pages source to "GitHub Actions" in
  `Settings → Pages → Build and deployment → Source` on the GitHub UI.
  The deploy workflow does the rest.

## Getting changes merged

1. Open a PR. CI runs lint, typecheck, tests, and the strict docs build.
2. A maintainer reviews. Squash-merge.
3. The docs auto-deploy when `main` updates.
