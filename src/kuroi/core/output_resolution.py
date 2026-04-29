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
