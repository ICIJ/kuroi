"""MkDocs hooks for the kuroi docs site.

Currently fixes one quirk: `mkdocs-typer2` injects raw `<code>[FOO]</code>`
elements whose `[text][id]` shape is then re-interpreted by `mkdocs-autorefs`
as an unresolved cross-reference (e.g. `[OPTIONS][PROVIDER]` → warning about
missing target `PROVIDER`). On the CLI reference page only, we unwrap the
resulting `<autoref>` tags back to their literal `[title][identifier]` text
so autorefs has nothing left to resolve.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mkdocs.config.defaults import MkDocsConfig
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page


def on_page_content(
    html: str,
    *,
    page: "Page",
    config: "MkDocsConfig",  # noqa: ARG001
    files: "Files",  # noqa: ARG001
) -> str | None:
    """Drop spurious `<autoref>` elements that mkdocs-typer2 induces.

    `mkdocs-typer2` injects raw `<code>[FOO]</code>` snippets that the
    surrounding `mkdocs-autorefs` inline processor mistakes for Markdown
    reference links (e.g. `[OPTIONS][PROVIDER]`). The result is an
    `<autoref identifier="PROVIDER">` tag that then triggers a
    "Could not find cross-reference target" warning at build time.

    On the CLI reference page only, we unwrap any `<autoref>` element
    back to its literal `[title][identifier]` so it renders as plain
    text — autorefs has nothing left to resolve.
    """
    if page.file.src_uri != "reference/cli.md":
        return None
    return _unwrap_autorefs(html)


_AUTOREF_RE = re.compile(
    r'<autoref [^>]*identifier=["\'](?P<id>[^"\']+)["\'][^>]*>(?P<title>.*?)</autoref>',
    re.DOTALL,
)


def _unwrap_autorefs(html: str) -> str:
    return _AUTOREF_RE.sub(
        lambda m: f"[{m.group('title')}][{m.group('id')}]", html
    )
