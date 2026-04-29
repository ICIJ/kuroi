"""Typer app and subcommand registration."""

import typer

import kuroi.cli.diff as _diff_module
import kuroi.cli.run as _run_module
from kuroi import __version__
from kuroi.cli.backups import backups_app
from kuroi.cli.config import config_app
from kuroi.cli.doctor import doctor_app
from kuroi.cli.setup import setup_app
from kuroi.cli.undo import undo_app
from kuroi.cli.verify import verify_app

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


app.add_typer(doctor_app, name="doctor", help="Check that everything is working.")
app.add_typer(verify_app, name="verify", help="Check an already-redacted PDF for leaks.")
app.command("run", help="Redact one or more PDFs.")(_run_module.run)
app.add_typer(undo_app, name="undo", help="Restore the most recent backup.")
app.add_typer(setup_app, name="setup", help="Interactively configure kuroi.")
app.add_typer(config_app, name="config", help="Configuration management.")
app.add_typer(backups_app, name="backups", help="Manage backups.")
app.command("diff", help="Show what changed between original and redacted.")(_diff_module.diff)
