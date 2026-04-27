"""Typer app and subcommand registration."""

import typer

from kuroi import __version__

app = typer.Typer(
    name="kuroi",
    help="Strip sensitive data from PDFs with LLM assistance.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"kuroi {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """kuroi — strip sensitive data from PDFs with LLM assistance."""
