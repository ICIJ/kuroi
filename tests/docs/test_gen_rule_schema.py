"""Tests for the rule-schema markdown generator."""

from __future__ import annotations

import importlib.util
import pathlib


def _load_module():
    path = pathlib.Path(__file__).parents[2] / "docs" / "_scripts" / "gen_rule_schema.py"
    spec = importlib.util.spec_from_file_location("gen_rule_schema", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_includes_top_level_fields() -> None:
    mod = _load_module()
    out = mod.render_schema_markdown()
    for field in ("name", "display_name", "description", "version", "categories"):
        assert f"`{field}`" in out, f"missing RuleSet field: {field}"


def test_render_includes_category_fields() -> None:
    mod = _load_module()
    out = mod.render_schema_markdown()
    for field in ("id", "label", "detection", "confidence", "pattern"):
        assert f"`{field}`" in out, f"missing Category field: {field}"


def test_render_includes_intro_paragraph() -> None:
    mod = _load_module()
    out = mod.render_schema_markdown()
    assert out.lstrip().startswith("# Rule schema")
    assert "auto-generated" in out.lower()
