import logging

from kuroi.core.log import setup_logging


def test_setup_logging_default_is_warning(caplog):
    setup_logging(verbosity=0, quiet=False)
    log = logging.getLogger("kuroi")
    log.info("hello")
    assert "hello" not in caplog.text


def test_setup_logging_v_emits_info(caplog):
    setup_logging(verbosity=1, quiet=False)
    log = logging.getLogger("kuroi")
    with caplog.at_level(logging.INFO, logger="kuroi"):
        log.info("info-only")
        log.debug("debug-only")
    assert "info-only" in caplog.text
    assert "debug-only" not in caplog.text


def test_setup_logging_vv_emits_debug(caplog):
    setup_logging(verbosity=2, quiet=False)
    log = logging.getLogger("kuroi")
    with caplog.at_level(logging.DEBUG, logger="kuroi"):
        log.debug("debug-here")
    assert "debug-here" in caplog.text


def test_setup_logging_quiet_silences_info(caplog):
    setup_logging(verbosity=1, quiet=True)
    log = logging.getLogger("kuroi")
    # No `caplog.at_level` here: the quiet contract is that setup_logging itself
    # raises the level above INFO. caplog should observe whatever the configured
    # logger lets through.
    log.info("should-be-silent")
    log.error("error-flows")
    # quiet wins over -v: INFO suppressed, ERROR still flows.
    assert "should-be-silent" not in caplog.text
    assert "error-flows" in caplog.text


def test_warn_vv_once_only_emits_once(capsys):
    from kuroi.core.log import _reset_vv_warning_flag, warn_vv_once

    _reset_vv_warning_flag()
    warn_vv_once()
    warn_vv_once()
    err = capsys.readouterr().err
    # Warning text appears exactly once even after two calls.
    assert err.count("note: -vv prints document text") == 1


def test_cli_vv_emits_warning_to_stderr():
    from typer.testing import CliRunner

    from kuroi.cli import app
    from kuroi.core.log import _reset_vv_warning_flag

    # `--version` is `is_eager=True` and exits before the main callback runs,
    # so we use `models --json` (a side-effect-free informational subcommand)
    # to actually exercise the main callback that calls `warn_vv_once`.
    _reset_vv_warning_flag()
    runner = CliRunner()
    result = runner.invoke(app, ["-vv", "models", "--json"])
    assert result.exit_code == 0
    assert "note: -vv prints document text" in result.stderr
