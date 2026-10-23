"""CLI entry point.

Examples:
  tradesignals --data alpaca --symbols AAPL,MSFT --strategy sma_cross            # signal only
  tradesignals --data xtb --broker xtb --symbols AAPL --strategy rsi_reversion --execute
  tradesignals --data alpaca --broker paper --symbols NVDA --strategy sma_cross --execute
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from .engine import SignalEngine
from .models import Tick, Timeframe
from .providers import BROKERS, DATA_PROVIDERS, make_broker, make_data
from .risk import RiskLimits, RiskManager
from .strategies import REGISTRY


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradesignals")
    p.add_argument("--data", choices=sorted(DATA_PROVIDERS), required=True)
    p.add_argument("--broker", choices=sorted(BROKERS), default=None)
    p.add_argument("--symbols", required=True, help="comma-separated canonical symbols, e.g. AAPL,MSFT")
    p.add_argument("--strategy", choices=sorted(REGISTRY), default="sma_cross")
    p.add_argument("--timeframe", choices=[t.value for t in Timeframe], default=None)
    p.add_argument("--execute", action="store_true", help="send orders (default: signals only)")
    p.add_argument("--qty", type=float, default=1.0, help="base position size in shares")
    p.add_argument("--no-warmup", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


async def _run(args: argparse.Namespace) -> None:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    kwargs = {"timeframe": Timeframe(args.timeframe)} if args.timeframe else {}
    strategy = REGISTRY[args.strategy](symbols, **kwargs)

    data = make_data(args.data)
    broker = make_broker(args.broker) if args.broker else None
    risk = RiskManager(RiskLimits.from_env()) if broker else None

    engine = SignalEngine(data, [strategy], broker=broker, risk=risk, execute=args.execute,
                          default_qty=args.qty, warmup=not args.no_warmup)

    # Paper broker needs marks; feed it the engine's last ticks.
    if broker is not None and broker.name == "paper":
        orig = engine._on_tick

        async def on_tick(t: Tick):
            broker.mark(t.symbol, t.price)  # type: ignore[attr-defined]
            await orig(t)
        engine._on_tick = on_tick  # type: ignore[method-assign]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, engine.stop)
    await engine.run()


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.execute and args.broker in ("alpaca", "xtb"):
        logging.warning("EXECUTE mode against %s — make sure this is a paper/demo account", args.broker)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
