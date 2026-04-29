"""kuroi config — configuration management subcommands."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import typer
from rich.console import Console

from kuroi.core.pricing import load_pricing

config_app = typer.Typer(help="Configuration management.")
console = Console()


def xdg_data_home() -> Path:
    """`$XDG_DATA_HOME` or `~/.local/share`."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def user_pricing_path() -> Path:
    return xdg_data_home() / "kuroi" / "pricing.json"


@config_app.command("refresh-pricing")
def refresh_pricing(
    from_path: Path = typer.Option(
        ..., "--from", exists=True, dir_okay=False, readable=True,
        help="Path to a pricing.json file to install.",
    ),
) -> None:
    """Replace the user's pricing.json with the contents of `--from`.

    The file is validated by attempting to load it through the same parser the
    rest of kuroi uses; on validation failure, no changes are made.
    """
    try:
        load_pricing(from_path)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        console.print(f"[red]Invalid pricing file:[/] {exc}")
        raise typer.Exit(code=2) from exc

    target = user_pricing_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(from_path, tmp)
    os.replace(tmp, target)
    console.print(f"  Wrote {target}")
