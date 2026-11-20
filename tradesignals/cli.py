"""CLI entry point.

Examples:
  tradesignals --data alpaca --symbols AAPL,MSFT --strategy sma_cross            # signal only
  tradesignals --data alpaca --broker paper --symbols NVDA --strategy sma_cross --execute
  tradesignals --data xtb --broker xtb --symbols AAPL --strategy rsi_reversion --execute
  tradesignals --data alpaca --broker paper --symbols AAPL,MSFT --execute --console  # with the admin console
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal

from . import __version__
from .engine import SignalEngine
from .events import EventBus
from .interfaces import Broker
from .models import Tick, Timeframe
from .providers import BROKERS, DATA_PROVIDERS, make_broker, make_data
from .risk import RiskLimits, RiskManager
from .store import Store, StoreWriter
from .strategies import REGISTRY

log = logging.getLogger("tradesignals.cli")

_EXAMPLES = (__doc__ or "").split("Examples:", 1)[-1]

DEFAULT_CONSOLE_PORT = 8788
DEFAULT_CONSOLE_DB = "data/tradesignals.db"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tradesignals",
        description="Provider-agnostic trade-signal engine. Signal-only unless --execute is given.",
        epilog="Examples:" + _EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--data", choices=sorted(DATA_PROVIDERS), required=True, help="market data provider")
    p.add_argument(
        "--broker",
        choices=sorted(BROKERS),
        default=None,
        help="broker to read positions from and, with --execute, send orders to (default: none)",
    )
    p.add_argument("--symbols", required=True, help="comma-separated canonical symbols, e.g. AAPL,MSFT")
    p.add_argument(
        "--strategy", choices=sorted(REGISTRY), default="sma_cross", help="strategy name (default: %(default)s)"
    )
    p.add_argument(
        "--timeframe",
        choices=[t.value for t in Timeframe],
        default=None,
        help="bar size for the strategy (default: the strategy's own)",
    )
    p.add_argument("--execute", action="store_true", help="send orders (default: signals only)")
    p.add_argument(
        "--allow-live", action="store_true", help="required to --execute against a live (non-paper, non-demo) account"
    )
    p.add_argument(
        "--qty",
        type=float,
        default=1.0,
        help="target position size in shares, scaled by signal strength (default: %(default)s)",
    )
    p.add_argument(
        "--no-warmup", action="store_true", help="skip replaying historical bars into the strategy at startup"
    )
    p.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="persist signals, orders, bars and equity to this SQLite file (default: $TRADESIGNALS_DB, else off)",
    )
    p.add_argument(
        "--console",
        action="store_true",
        help="serve the admin console on http://127.0.0.1:<port> (needs the 'console' extra; implies --db)",
    )
    p.add_argument(
        "--console-port",
        type=int,
        default=None,
        metavar="PORT",
        help=f"port for --console (default: $CONSOLE_PORT, else {DEFAULT_CONSOLE_PORT})",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="DEBUG logging for tradesignals loggers (third-party libraries stay at INFO)",
    )
    return p


def configure_logging(verbose: bool) -> None:
    """Root stays at INFO so third-party libraries never log at DEBUG: websockets would dump
    every frame, including the XTB login payload. -v raises only the tradesignals loggers."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("tradesignals").setLevel(logging.DEBUG if verbose else logging.INFO)


def check_live_guard(execute: bool, allow_live: bool, broker: Broker | None) -> None:
    """Refuse to execute against a live account unless --allow-live was given."""
    if not execute or broker is None:
        return
    if broker.paper:
        log.warning("EXECUTE mode: broker=%s paper=True", broker.name)
        return
    if not allow_live:
        raise SystemExit(
            f"refusing --execute: broker '{broker.name}' is configured for a LIVE account "
            "(ALPACA_PAPER / XTB_DEMO is false). Re-run with --allow-live if that is really what you want."
        )
    log.warning("EXECUTE mode against a LIVE %s account (--allow-live given)", broker.name)


def _paper_mark_callback(broker: Broker | None):
    """PaperBroker needs marks to fill orders; feed it every tick the engine sees."""
    mark = getattr(broker, "mark", None)
    if not callable(mark):
        return None

    def on_tick(t: Tick) -> None:
        mark(t.symbol, t.price)

    return on_tick


async def _run(args: argparse.Namespace) -> None:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    kwargs = {"timeframe": Timeframe(args.timeframe)} if args.timeframe else {}
    strategy = REGISTRY[args.strategy](symbols, **kwargs)

    data = make_data(args.data)
    broker = make_broker(args.broker) if args.broker else None
    check_live_guard(args.execute, args.allow_live, broker)
    risk = RiskManager(RiskLimits.from_env()) if broker else None
    if args.execute and broker is None:
        log.warning("--execute given without --broker: running signal-only")

    db_path = args.db or os.environ.get("TRADESIGNALS_DB") or None
    if args.console and not db_path:
        db_path = DEFAULT_CONSOLE_DB  # the console reads its history from the database
    bus = EventBus() if db_path else None

    engine = SignalEngine(
        data,
        [strategy],
        broker=broker,
        risk=risk,
        execute=args.execute,
        default_qty=args.qty,
        warmup=not args.no_warmup,
        on_tick=_paper_mark_callback(broker),
        bus=bus,
    )

    store: Store | None = None
    writer: StoreWriter | None = None
    if db_path and bus:
        store = Store(db_path)
        pruned = store.prune()
        if any(pruned.values()):
            log.info("pruned old rows: %s", pruned)
        run_id = store.start_run(
            data=data.name,
            broker=broker.name if broker else "",
            paper=broker.paper if broker else None,
            execute=engine.execute,
            symbols=symbols,
            strategies=[strategy.name],
        )
        writer = StoreWriter(store, bus, run_id)
        writer.start()
        log.info("persisting to %s (run %s)", db_path, run_id)

    console = None
    if args.console and store and writer and bus:
        console = _start_console(args, engine, store, writer, bus)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, engine.stop)  # clean stop: cancels nothing, flattens nothing
    try:
        await engine.run()
    finally:
        if console:
            service, server = console
            await service.stop()
            await server.stop()
        if writer and store:
            await writer.stop()
            store.end_run(writer.run_id)
            store.close()


def _start_console(args: argparse.Namespace, engine: SignalEngine, store: Store, writer: StoreWriter, bus: EventBus):
    """Start the admin console next to the engine. Returns (service, server), or None if it cannot run."""
    try:
        from .console import ConsoleServer, ConsoleService, create_app
    except ImportError as e:
        raise SystemExit(f"--console needs the console extra: pip install -e '.[console]' ({e})") from e

    port = args.console_port or int(os.environ.get("CONSOLE_PORT") or DEFAULT_CONSOLE_PORT)
    token = os.environ.get("CONSOLE_TOKEN") or None
    service = ConsoleService(engine, store, bus, run_id=writer.run_id)
    service.start(writer)
    server = ConsoleServer(create_app(service, token=token), port)
    server.start()
    log.info("console: %s (%s)", server.url, "actions enabled" if token else "read-only, CONSOLE_TOKEN not set")
    return service, server


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    args = build_parser().parse_args()
    configure_logging(args.verbose)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
