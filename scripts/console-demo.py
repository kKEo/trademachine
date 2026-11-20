#!/usr/bin/env python3
"""Run the admin console against a synthetic market: no keys, no network, no real money.

A random-walk feed replays the last few hours at high speed, then continues in real time.
Two strategies trade an in-memory paper broker, so every console page has something to show.

Usage:
  python scripts/console-demo.py                  # http://127.0.0.1:8788
  CONSOLE_TOKEN=demo python scripts/console-demo.py --port 8900 --db /tmp/demo.db

Needs the console extra: pip install -e ".[console]"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import random
import signal
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timedelta

from tradesignals.analytics import resample
from tradesignals.bars import bucket_start
from tradesignals.cli import configure_logging
from tradesignals.console import ConsoleServer, ConsoleService, create_app
from tradesignals.engine import SignalEngine
from tradesignals.events import EventBus
from tradesignals.interfaces import MarketDataProvider
from tradesignals.models import Bar, Tick, Timeframe, utcnow
from tradesignals.providers.fake import PaperBroker
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.store import Store, StoreWriter
from tradesignals.strategies import RsiReversion, SmaCross

log = logging.getLogger("tradesignals.demo")

START_PRICES = {"AAPL": 228.0, "MSFT": 431.0, "NVDA": 142.0, "KO": 63.5}


class DemoFeed(MarketDataProvider):
    """Synthetic ticks: a random walk on top of a slow wave, so trends form and reverse."""

    name = "demo"

    def __init__(self, symbols: Iterable[str], hours_back: float = 5.0, step_sec: int = 15, speed: float = 0.02):
        self.symbols = list(symbols)
        self.step = timedelta(seconds=step_sec)
        self.speed = speed  # real seconds per simulated tick while catching up
        self._rng = random.Random(7)
        self._start = utcnow() - timedelta(hours=hours_back)
        self._i = 0
        self._price = {s: START_PRICES.get(s, 100.0) for s in self.symbols}
        self._phase = {s: self._rng.uniform(0, 6.28) for s in self.symbols}
        self._history = {s: self._make_history(s) for s in self.symbols}

    def _next_price(self, symbol: str, i: int) -> float:
        p = self._price[symbol]
        drift = math.sin(i / 90 + self._phase[symbol]) * 0.00012
        p *= 1 + drift + self._rng.gauss(0, 0.0007)
        self._price[symbol] = p
        return p

    def _make_history(self, symbol: str, bars: int = 400) -> list[Bar]:
        """One-minute bars that end where the live walk begins."""
        out: list[Bar] = []
        start = bucket_start(self._start, Timeframe.M1) - timedelta(minutes=bars)
        for n in range(bars):
            prices = [self._next_price(symbol, n * 4 + k - bars * 4) for k in range(4)]
            out.append(
                Bar(
                    symbol,
                    Timeframe.M1,
                    prices[0],
                    max(prices),
                    min(prices),
                    prices[-1],
                    float(self._rng.randint(500, 5000)),
                    start + timedelta(minutes=n),
                    self.name,
                )
            )
        return out

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def subscribe(self, symbols: Iterable[str]) -> None: ...

    async def stream(self) -> AsyncIterator[Tick]:
        ts = self._start
        while True:
            now = utcnow()
            if ts < now:  # catching up with the wall clock
                await asyncio.sleep(self.speed)
            else:
                await asyncio.sleep((ts - now).total_seconds())
            self._i += 1
            for s in self.symbols:
                p = self._next_price(s, self._i)
                spread = p * 0.0001
                yield Tick(s, round(p - spread, 2), round(p + spread, 2), ts, self.name)
            ts += self.step

    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime | None = None
    ) -> list[Bar]:
        bars = resample(self._history.get(symbol, []), timeframe)
        return [b for b in bars if b.start >= start and (end is None or b.start <= end)]


async def run(args: argparse.Namespace) -> None:
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    data = DemoFeed(symbols)
    broker = PaperBroker()
    bus = EventBus()
    strategies = [
        SmaCross(symbols[: max(1, len(symbols) - 1)], fast=8, slow=21),
        RsiReversion(symbols[-1:], n=14, timeframe=Timeframe.M5),
    ]
    # The replayed part of the feed carries timestamps from hours ago, so the stale-quote rule is off.
    risk = RiskManager(RiskLimits(max_order_notional=5000, max_quote_age_sec=10**9, max_daily_loss=2000))
    engine = SignalEngine(
        data,
        strategies,
        broker=broker,
        risk=risk,
        execute=True,
        default_qty=args.qty,
        on_tick=lambda t: broker.mark(t.symbol, t.price),
        bus=bus,
        snapshot_interval=2.0,
        quote_interval=0.5,
    )

    store = Store(args.db)
    run_id = store.start_run(
        data=data.name,
        broker=broker.name,
        paper=True,
        execute=True,
        symbols=symbols,
        strategies=[st.name for st in strategies],
    )
    writer = StoreWriter(store, bus, run_id)
    writer.start()
    service = ConsoleService(engine, store, bus, run_id=run_id)
    service.start(writer)
    server = ConsoleServer(create_app(service, token=os.environ.get("CONSOLE_TOKEN") or None), args.port)
    server.start()
    log.info("demo console: %s", server.url)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, engine.stop)
    try:
        await engine.run()
    finally:
        await service.stop()
        await server.stop()
        await writer.stop()
        store.end_run(run_id)
        store.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--db", default=":memory:", help="SQLite file (default: in memory, gone when the demo stops)")
    ap.add_argument("--symbols", default="AAPL,MSFT,NVDA,KO")
    ap.add_argument("--qty", type=float, default=10.0)
    args = ap.parse_args()
    configure_logging(verbose=False)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
