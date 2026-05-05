"""Tests for the CLI reference markdown generator."""

from __future__ import annotations

import importlib.util
import pathlib
import re


def _load_module():
    path = pathlib.Path(__file__).parents[2] / "docs" / "_scripts" / "gen_cli_reference.py"
    spec = importlib.util.spec_from_file_location("gen_cli_reference", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_starts_with_banner() -> None:
    mod = _load_module()
    out = mod.render_cli_markdown()
    assert out.startswith("<!-- DO NOT EDIT")


def test_render_contains_h1_after_banner() -> None:
    mod = _load_module()
    out = mod.render_cli_markdown()
    assert "\n# CLI reference" in out


def test_render_includes_every_subcommand() -> None:
    mod = _load_module()
    out = mod.render_cli_markdown()
    for sub in ("run", "diff", "models", "doctor", "verify", "undo", "setup", "config", "backups"):
        assert f"## `kuroi {sub}`" in out, f"missing subcommand section: {sub}"


def test_render_is_deterministic() -> None:
    mod = _load_module()
    assert mod.render_cli_markdown() == mod.render_cli_markdown()


def test_render_fences_contain_help_text() -> None:
    mod = _load_module()
    out = mod.render_cli_markdown()
    fences = re.findall(r"```\n(.*?)\n```", out, re.DOTALL)
    assert len(fences) >= 10, f"expected at least 10 fences, got {len(fences)}"
    assert all(f.strip() for f in fences), "one or more fences are empty"
    assert "Usage: kuroi" in fences[0], "root fence missing 'Usage: kuroi'"


def test_render_does_not_mutate_app_rich_markup_mode() -> None:
    """Render must not leave `app.rich_markup_mode` flipped off for later tests."""
    from kuroi.cli import app

    before = app.rich_markup_mode
    mod = _load_module()
    mod.render_cli_markdown()
    assert app.rich_markup_mode == before
