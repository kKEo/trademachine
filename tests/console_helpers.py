"""Shared set-up for console tests: a replay-fed engine, an in-memory store and the ASGI app.

No sockets: requests go straight into the app through httpx's ASGI transport.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from helpers import ticks, wave

from tradesignals.console import ConsoleService, create_app
from tradesignals.engine import SignalEngine
from tradesignals.events import EventBus
from tradesignals.providers.fake import PaperBroker, ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.store import Store, StoreWriter
from tradesignals.strategies import SmaCross

BASE = "http://127.0.0.1:8788"


class Rig:
    """Everything a console test needs, already wired together."""

    def __init__(self, events=None, execute=True, broker=True, token=None, limits=None, strategies=None, **engine_kw):
        self.bus = EventBus()
        self.store = Store()
        self.broker = PaperBroker() if broker else None
        self.data = ReplayProvider(events if events is not None else ticks("AAPL", wave(120)))
        self.risk = (
            RiskManager(limits or RiskLimits(max_order_notional=10_000, max_quote_age_sec=10**9)) if broker else None
        )
        self.engine = SignalEngine(
            self.data,
            strategies or [SmaCross(["AAPL"], fast=5, slow=15)],
            broker=self.broker,
            risk=self.risk,
            execute=execute,
            default_qty=3,
            warmup=False,
            on_tick=(lambda t: self.broker.mark(t.symbol, t.price)) if self.broker else None,
            bus=self.bus,
            **engine_kw,
        )
        self.run_id = self.store.start_run(data="replay", broker="paper" if broker else "", paper=True, execute=execute)
        self.writer = StoreWriter(self.store, self.bus, self.run_id)
        self.service = ConsoleService(self.engine, self.store, self.bus, run_id=self.run_id)
        self.token = token
        self.app = create_app(self.service, token=token)

    def client(self, **kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE, **kw)

    async def run_engine(self) -> None:
        """Run the replay to its end and wait until everything is in the database."""
        self.writer.start()
        self.service.start(self.writer)
        await self.engine.run()
        await self.flush()

    async def flush(self) -> None:
        for _ in range(200):
            await asyncio.sleep(0.005)
            if self.writer._sub._queue.empty():
                break
        await asyncio.sleep(0.02)

    async def close(self) -> None:
        await self.service.stop()
        await self.writer.stop()


@asynccontextmanager
async def finished_run(**kw):
    """A rig whose replay has run to completion: history in the store, final state in the engine."""
    rig = Rig(**kw)
    await rig.run_engine()
    try:
        yield rig
    finally:
        await rig.close()
