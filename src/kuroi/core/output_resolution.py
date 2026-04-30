"""Output path collision handling.

`resolve_output_path` returns the final path kuroi should write to, given the
user's CLI flags. Collisions raise `OutputCollisionError` with a concrete
suggestion the CLI prints. The suggestion algorithm is `suggest_versioned_name`,
which lives separately so it can be tested without involving CLI machinery.
"""

from __future__ import annotations

import re
from pathlib import Path

_VERSIONED_RE = re.compile(r"^(?P<stem>.+)\.v(?P<n>\d+)$")


def suggest_versioned_name(target: Path) -> Path:
    """Return `<base>.v<N>.<ext>` where N is `max(existing) + 1`, or 2 if none."""
    parent = target.parent
    base = target.stem
    suffix = target.suffix
    versions: list[int] = []
    for sibling in parent.glob(f"{base}.v*{suffix}"):
        m = _VERSIONED_RE.match(sibling.stem)
        if m and m.group("stem") == base:
            try:
                versions.append(int(m.group("n")))
            except ValueError:
                continue
    next_n = max(versions) + 1 if versions else 2
    return parent / f"{base}.v{next_n}{suffix}"


class OutputResolutionError(Exception):
    """User passed an invalid combination of `-o`/`--in-place`/`--overwrite`."""


class OutputCollisionError(Exception):
    """The output path exists and `--overwrite` was not passed."""

    def __init__(self, target: Path, suggestion: Path) -> None:
        super().__init__(f"output path {target} exists; pass --overwrite or use {suggestion}")
        self.target = target
        self.suggestion = suggestion


def resolve_output_path(
    pdf: Path,
    *,
    output: Path | None,
    in_place: bool,
    overwrite: bool,
) -> Path:
    """Decide the final write path. Validates flag combinations and collisions."""
    if in_place and output is not None:
        raise OutputResolutionError("--in-place and -o are mutually exclusive")
    if in_place:
        return pdf
    if output is None:
        raise OutputResolutionError("must pass either -o <path> or --in-place")
    if output.resolve() == pdf.resolve():
        raise OutputResolutionError(
            "output path equals input; use --in-place to overwrite the input"
        )
    if output.exists() and not overwrite:
        raise OutputCollisionError(output, suggest_versioned_name(output))
    return output
