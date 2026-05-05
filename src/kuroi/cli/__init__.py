"""Typer app and subcommand registration."""

import typer

import kuroi.cli.diff as _diff_module
import kuroi.cli.models as _models_module
import kuroi.cli.run as _run_module
from kuroi import __version__
from kuroi.cli.backups import backups_app
from kuroi.cli.config import config_app
from kuroi.cli.doctor import doctor_app
from kuroi.cli.setup import setup_app
from kuroi.cli.undo import undo_app
from kuroi.cli.verify import verify_app
from kuroi.core.log import setup_logging, warn_vv_once

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
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        help="Show more detail (use -vv for debug).",
    ),
    quiet: bool = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Show less.",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """kuroi — strip sensitive data from PDFs with LLM assistance."""
    setup_logging(verbosity=verbose, quiet=quiet)
    if verbose >= 2:
        warn_vv_once()


app.add_typer(doctor_app, name="doctor", help="Check that everything is working.")
app.add_typer(verify_app, name="verify", help="Check an already-redacted PDF for leaks.")
app.command("run", help="Redact a PDF.")(_run_module.run)
app.add_typer(undo_app, name="undo", help="Restore the most recent backup.")
app.add_typer(setup_app, name="setup", help="Interactively configure kuroi.")
app.add_typer(config_app, name="config", help="Configuration management.")
app.add_typer(backups_app, name="backups", help="Manage backups.")
app.command("diff", help="Show what changed between original and redacted.")(_diff_module.diff)
app.command("models", help="List available LLM providers and models.")(_models_module.models)
