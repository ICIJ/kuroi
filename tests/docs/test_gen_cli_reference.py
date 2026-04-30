"""Tests for the CLI reference markdown generator."""

from __future__ import annotations

import importlib.util
import pathlib


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
