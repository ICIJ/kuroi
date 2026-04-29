from pathlib import Path

import pytest

from kuroi.core.output_resolution import suggest_versioned_name


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
