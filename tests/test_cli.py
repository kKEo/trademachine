import logging
import subprocess
import sys

import pytest

from tradesignals import __version__
from tradesignals.cli import build_parser, check_live_guard, configure_logging
from tradesignals.providers.fake import PaperBroker


class LiveBroker(PaperBroker):
    name = "alpaca"
    paper = False


def test_parser_defaults_and_flags():
    args = build_parser().parse_args(["--data", "alpaca", "--symbols", "AAPL"])
    assert (
        args.broker,
        args.strategy,
        args.timeframe,
        args.execute,
        args.allow_live,
        args.qty,
        args.no_warmup,
        args.verbose,
    ) == (None, "sma_cross", None, False, False, 1.0, False, False)
    args = build_parser().parse_args(
        [
            "--data",
            "xtb",
            "--broker",
            "xtb",
            "--symbols",
            "AAPL",
            "--strategy",
            "rsi_reversion",
            "--timeframe",
            "5m",
            "--execute",
            "--allow-live",
            "--qty",
            "2.5",
            "--no-warmup",
            "-v",
        ]
    )
    assert (
        args.broker,
        args.strategy,
        args.timeframe,
        args.execute,
        args.allow_live,
        args.qty,
        args.no_warmup,
        args.verbose,
    ) == ("xtb", "rsi_reversion", "5m", True, True, 2.5, True, True)


def test_parser_rejects_unknown_choices():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--data", "replay", "--symbols", "AAPL"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--data", "alpaca", "--symbols", "AAPL", "--timeframe", "2m"])


def test_live_guard_refuses_execute_on_live_account_without_allow_live():
    with pytest.raises(SystemExit) as e:
        check_live_guard(True, False, LiveBroker())
    assert "LIVE" in str(e.value) and "--allow-live" in str(e.value)


def test_live_guard_allows_paper_broker_signal_only_and_no_broker():
    check_live_guard(True, False, PaperBroker())
    check_live_guard(False, False, LiveBroker())
    check_live_guard(True, False, None)


def test_live_guard_allows_live_with_flag_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="tradesignals.cli"):
        check_live_guard(True, True, LiveBroker())
    assert any("LIVE" in r.getMessage() for r in caplog.records)


def test_base_broker_defaults_to_not_paper():
    class Forgetful(PaperBroker):
        paper = PaperBroker.__mro__[1].paper  # the Broker ABC default

    with pytest.raises(SystemExit):
        check_live_guard(True, False, Forgetful())


def test_verbose_only_raises_tradesignals_loggers():
    configure_logging(verbose=True)
    assert logging.getLogger("tradesignals").level == logging.DEBUG
    assert logging.getLogger("tradesignals.xtb").getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger("websockets").getEffectiveLevel() >= logging.INFO
    assert logging.getLogger("websockets.client").getEffectiveLevel() >= logging.INFO
    configure_logging(verbose=False)
    assert logging.getLogger("tradesignals").level == logging.INFO


def test_module_entry_point_prints_version():
    out = subprocess.run(
        [sys.executable, "-m", "tradesignals", "--version"], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == f"tradesignals {__version__}"


def test_help_lists_every_flag():
    text = build_parser().format_help()
    for flag in (
        "--data",
        "--broker",
        "--symbols",
        "--strategy",
        "--timeframe",
        "--execute",
        "--allow-live",
        "--qty",
        "--no-warmup",
        "--verbose",
        "--version",
    ):
        assert flag in text
