"""SignalEngine: the only place where data, strategies, risk and broker meet.

Flow per event:
    provider.stream() -> Tick/Bar
        -> BarAggregator (per symbol, per strategy timeframe)
        -> Strategy.on_bar / on_tick -> Signal
        -> SignalSink (log, callbacks)
        -> [execute mode] target position -> OrderRequest -> RiskManager -> Broker
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Awaitable

from .bars import BarAggregator
from .interfaces import Broker, MarketDataProvider
from .models import (
    Account, Bar, Order, OrderRequest, Position, Side, Signal, SignalKind, Tick, Timeframe, utcnow,
)
from .risk import RiskManager
from .strategies import Strategy

log = logging.getLogger("tradesignals.engine")

SignalCallback = Callable[[Signal], Awaitable[None] | None]


class SignalEngine:
    def __init__(
        self,
        data: MarketDataProvider,
        strategies: list[Strategy],
        broker: Broker | None = None,
        risk: RiskManager | None = None,
        execute: bool = False,
        default_qty: float = 1.0,
        on_signal: SignalCallback | None = None,
        warmup: bool = True,
    ):
        self.data = data
        self.strategies = strategies
        self.broker = broker
        self.risk = risk
        self.execute = execute and broker is not None
        self.default_qty = default_qty
        self.on_signal = on_signal
        self.warmup = warmup

        self.symbols = sorted({s for st in strategies for s in st.symbols})
        self.last_tick: dict[str, Tick] = {}
        self.signals: list[Signal] = []
        self.orders: list[Order] = []
        self._aggs: dict[tuple[str, Timeframe], BarAggregator] = {}
        self._positions: dict[str, Position] = {}
        self._account: Account | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        await self.data.connect()
        if self.broker:
            await self.broker.connect()
            await self._refresh_broker_state()
        if self.warmup:
            await self._warmup()
        await self.data.subscribe(self.symbols)
        log.info("engine running: symbols=%s execute=%s", self.symbols, self.execute)

        tasks = [asyncio.create_task(self._consume_data())]
        if self.broker:
            tasks.append(asyncio.create_task(self._consume_order_updates()))
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.data.close()
            if self.broker:
                await self.broker.close()

    def stop(self) -> None:
        self._stop.set()

    async def kill_switch(self) -> None:
        """Cancel everything, halt risk, stop the loop."""
        if self.risk:
            self.risk.halted, self.risk.halt_reason = True, "kill switch"
        if self.broker:
            await self.broker.cancel_all()
        self.stop()

    # ------------------------------------------------------------------ warm-up
    async def _warmup(self) -> None:
        for st in self.strategies:
            if st.warmup_bars <= 0:
                continue
            start = utcnow() - timedelta(seconds=st.timeframe.seconds * st.warmup_bars * 3)
            for sym in st.symbols:
                try:
                    bars = await self.data.get_bars(sym, st.timeframe, start)
                except Exception as e:  # noqa: BLE001
                    log.warning("warmup %s/%s failed: %s", st.name, sym, e)
                    continue
                for b in bars[-st.warmup_bars:]:
                    st.on_bar(b)  # signals during warm-up are discarded
                log.info("warmup %s/%s: %d bars", st.name, sym, len(bars))

    # ------------------------------------------------------------------ data loop
    async def _consume_data(self) -> None:
        async for ev in self.data.stream():
            try:
                if isinstance(ev, Tick):
                    await self._on_tick(ev)
                elif isinstance(ev, Bar):
                    await self._on_bar(ev)
            except Exception:  # noqa: BLE001
                log.exception("error handling %s", ev)
        self.stop()

    def _agg(self, symbol: str, tf: Timeframe) -> BarAggregator:
        key = (symbol, tf)
        if key not in self._aggs:
            self._aggs[key] = BarAggregator(symbol, tf)
        return self._aggs[key]

    async def _on_tick(self, tick: Tick) -> None:
        self.last_tick[tick.symbol] = tick
        for st in self.strategies:
            if tick.symbol not in st.symbols:
                continue
            sig = st.on_tick(tick)
            if sig:
                await self._emit(sig)
            done = self._agg(tick.symbol, st.timeframe).on_tick(tick)
            if done:
                sig = st.on_bar(done)
                if sig:
                    await self._emit(sig)

    async def _on_bar(self, bar: Bar) -> None:
        for st in self.strategies:
            if bar.symbol not in st.symbols:
                continue
            done = self._agg(bar.symbol, st.timeframe).on_bar(bar)
            if done:
                sig = st.on_bar(done)
                if sig:
                    await self._emit(sig)

    # ------------------------------------------------------------------ signals
    async def _emit(self, sig: Signal) -> None:
        self.signals.append(sig)
        log.info("SIGNAL %s %s %s strength=%.2f %s", sig.strategy, sig.symbol, sig.kind.value,
                 sig.strength, sig.reason)
        if self.on_signal:
            r = self.on_signal(sig)
            if asyncio.iscoroutine(r):
                await r
        if self.execute:
            await self._act(sig)

    async def _act(self, sig: Signal) -> None:
        assert self.broker is not None
        await self._refresh_broker_state()
        cur = self._positions.get(sig.symbol)
        cur_qty = cur.qty if cur else 0.0
        target = {
            SignalKind.LONG: self.default_qty * sig.strength,
            SignalKind.SHORT: -self.default_qty * sig.strength,
            SignalKind.FLAT: 0.0,
        }[sig.kind]
        delta = target - cur_qty
        if abs(delta) < 1e-9:
            log.info("no-op: %s already at %.2f", sig.symbol, cur_qty)
            return
        req = OrderRequest(
            symbol=sig.symbol,
            side=Side.BUY if delta > 0 else Side.SELL,
            qty=abs(round(delta, 4)),
            note=f"{sig.strategy}:{sig.kind.value}",
        )
        if self.risk:
            ok, why = self.risk.check(req, self.last_tick.get(sig.symbol),
                                      list(self._positions.values()), self._account)
            if not ok:
                log.warning("RISK REJECT %s %s %s: %s", req.side.value, req.qty, req.symbol, why)
                return
        try:
            order = await self.broker.submit_order(req)
        except Exception:  # noqa: BLE001
            log.exception("submit failed for %s", req)
            return
        self.orders.append(order)
        log.info("ORDER %s %s %.4f %s -> %s (%s)", order.source, order.side.value, order.qty,
                 order.symbol, order.status.value, order.id)

    async def _refresh_broker_state(self) -> None:
        assert self.broker is not None
        try:
            self._positions = {p.symbol: p for p in await self.broker.get_positions()}
            self._account = await self.broker.get_account()
        except Exception as e:  # noqa: BLE001
            log.warning("broker state refresh failed: %s", e)

    async def _consume_order_updates(self) -> None:
        assert self.broker is not None
        async for o in self.broker.order_updates():
            log.info("ORDER UPDATE %s %s %s filled=%.4f @ %s", o.symbol, o.side.value,
                     o.status.value, o.filled_qty, o.avg_fill_price)
            if o.status.is_terminal:
                await self._refresh_broker_state()
