from pathlib import Path

import pytest

from kuroi.core.output_resolution import (
    OutputCollisionError,
    OutputResolutionError,
    resolve_output_path,
    suggest_versioned_name,
)


def test_suggest_v2_when_no_existing_version(tmp_path: Path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF")
    suggestion = suggest_versioned_name(target)
    assert suggestion == tmp_path / "report.v2.pdf"


def test_suggest_next_when_versions_exist(tmp_path: Path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"%PDF")
    (tmp_path / "report.v2.pdf").write_bytes(b"%PDF")
    (tmp_path / "report.v3.pdf").write_bytes(b"%PDF")
    suggestion = suggest_versioned_name(target)
    assert suggestion == tmp_path / "report.v4.pdf"


def test_suggest_skips_non_int_versions(tmp_path: Path):
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF")
    (tmp_path / "doc.v2.pdf").write_bytes(b"%PDF")
    (tmp_path / "doc.vfoo.pdf").write_bytes(b"%PDF")  # ignored
    suggestion = suggest_versioned_name(target)
    assert suggestion == tmp_path / "doc.v3.pdf"


def test_resolve_in_place(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    final = resolve_output_path(src, output=None, in_place=True, overwrite=False)
    assert final == src


def test_resolve_with_output_to_fresh_path(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    final = resolve_output_path(src, output=out, in_place=False, overwrite=False)
    assert final == out


def test_resolve_collision_raises_with_suggestion(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    out.write_bytes(b"%PDF")
    with pytest.raises(OutputCollisionError) as exc:
        resolve_output_path(src, output=out, in_place=False, overwrite=False)
    assert exc.value.suggestion == tmp_path / "y.v2.pdf"


def test_resolve_collision_overwrite_allows(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    out.write_bytes(b"%PDF")
    final = resolve_output_path(src, output=out, in_place=False, overwrite=True)
    assert final == out


def test_resolve_in_place_with_output_is_error(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    out = tmp_path / "y.pdf"
    with pytest.raises(OutputResolutionError, match="mutually exclusive"):
        resolve_output_path(src, output=out, in_place=True, overwrite=False)


def test_resolve_no_output_no_in_place_is_error(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(OutputResolutionError, match="-o"):
        resolve_output_path(src, output=None, in_place=False, overwrite=False)


def test_resolve_input_equals_output_is_error(tmp_path: Path):
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(OutputResolutionError, match="--in-place"):
        resolve_output_path(src, output=src, in_place=False, overwrite=False)
