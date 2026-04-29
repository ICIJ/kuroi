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
