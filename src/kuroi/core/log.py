"""Verbosity plumbing.

`setup_logging(verbosity, quiet)` configures the `kuroi` logger so that:

  default       -> WARNING (only warnings/errors)
  -v            -> INFO   (findings list, token counts, rule fire counts, sizes)
  -vv           -> DEBUG  (full prompts, full responses, HTTP timing, traces)
  -q (any -v)   -> ERROR  (suppresses everything except final result + errors)

Calls to `console.print(...)` for user-facing default output are unaffected.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(verbosity: int, quiet: bool) -> None:
    if quiet:
        level = logging.ERROR
    elif verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    root = logging.getLogger("kuroi")
    root.setLevel(level)
    # Idempotent — clear and re-attach a single stderr handler.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    # Keep propagation enabled so test fixtures (pytest caplog attaches at root)
    # can observe `kuroi.*` log records. The duplicate-emission risk is bounded
    # because the root logger has no handlers in normal CLI usage.
    root.propagate = True


_VV_WARNED = False


def warn_vv_once() -> None:
    """Emit the document-text warning the first time -vv is in use this process."""
    global _VV_WARNED
    if _VV_WARNED:
        return
    _VV_WARNED = True
    print(
        "note: -vv prints document text to stderr; redirect if recording",
        file=sys.stderr,
    )


def _reset_vv_warning_flag() -> None:
    """Test hook only — do not call from production code."""
    global _VV_WARNED
    _VV_WARNED = False
