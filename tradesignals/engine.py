"""SignalEngine: the only place where data, strategies, risk and broker meet.

Flow per event:
    provider.stream() -> Tick/Bar
        -> BarAggregator (per symbol, per strategy timeframe)
        -> Strategy.on_bar / on_tick -> Signal
        -> SignalSink (log, callbacks, event bus)
        -> [execute mode] target position -> OrderRequest -> RiskManager -> Broker

Bar sources: ticks drive a symbol's aggregators only until the provider pushes its own bars
for that symbol; from then on provider bars win. Alpaca pushes both quotes and 1-minute bars,
and feeding both into one aggregator would hand every bucket to the strategy twice.

Observability: when an ``EventBus`` is given, the engine publishes signals, their outcomes,
orders, completed bars, throttled quotes and periodic account snapshots to it. Publishing never
blocks; without a bus the engine behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Awaitable
from uuid import uuid4

from .bars import BarAggregator
from .events import (
    BarEvent,
    Event,
    EventBus,
    OrderEvent,
    OutcomeKind,
    QuoteEvent,
    SignalEvent,
    SignalOutcome,
    SnapshotEvent,
    StatusEvent,
)
from .interfaces import Broker, MarketDataProvider
from .models import (
    Account,
    Bar,
    Order,
    OrderRequest,
    Position,
    Side,
    Signal,
    SignalKind,
    Tick,
    Timeframe,
    utcnow,
)
from .risk import RiskManager
from .strategies import Strategy

log = logging.getLogger("tradesignals.engine")

SignalCallback = Callable[[Signal], Awaitable[None] | None]
TickCallback = Callable[[Tick], None]

#: How many signals/orders the engine keeps in memory; the store holds the full history.
MAX_HISTORY = 1000
#: An order still open after this many seconds is re-read from the broker before the next order.
OPEN_ORDER_RECHECK_SEC = 30.0
#: ...and forgotten after this many seconds if the broker cannot tell us about it.
OPEN_ORDER_FORGET_SEC = 600.0


class ActionRefused(Exception):
    """An operator action (halt, flatten, manual order, ...) cannot be carried out in this state."""


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
        on_tick: TickCallback | None = None,
        bus: EventBus | None = None,
        snapshot_interval: float = 10.0,
        quote_interval: float = 1.0,
    ):
        self.data = data
        self.strategies = list(strategies)
        self._strategy_ids: dict[str, Strategy] = {}  # stable handle per running strategy, e.g. "sma_cross-1"
        self._strategy_seq: dict[str, int] = {}
        self._paused: set[str] = set()
        for st in self.strategies:
            self._register(st)
        self.broker = broker
        self.risk = risk
        self.execute = execute and broker is not None
        self.default_qty = default_qty
        self.on_signal = on_signal
        self.on_tick = on_tick  # e.g. feed PaperBroker.mark; called for every tick before strategies
        self.warmup = warmup
        self.bus = bus
        self.snapshot_interval = snapshot_interval  # seconds between account snapshots / heartbeats
        self.quote_interval = quote_interval  # at most one QuoteEvent per symbol in this many seconds
        self.started_at: datetime | None = None
        self.running = False

        self.symbols = sorted({s for st in strategies for s in st.symbols})
        self.last_tick: dict[str, Tick] = {}
        self.signals: list[Signal] = []
        self.orders: list[Order] = []
        self._aggs: dict[tuple[str, Timeframe], BarAggregator] = {}
        self._ticks_build_bars = not getattr(data, "pushes_bars", False)
        self._bar_symbols: set[str] = set()  # symbols seen pushing bars (safety net for undeclared providers)
        self._positions: dict[str, Position] = {}
        self._account: Account | None = None
        self._stop = asyncio.Event()
        self._quote_sent: dict[str, float] = {}
        self._open_orders: dict[str, Order] = {}  # orders in flight: submitted, not yet terminal
        self._order_lock = asyncio.Lock()  # one sizing-and-submit at a time (signals, flatten, manual)

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        tasks: list[asyncio.Task] = []
        try:
            await self.data.connect()
            if self.broker:
                await self.broker.connect()
                await self._refresh_broker_state()
            if self.warmup:
                await self._warmup()
            await self.data.subscribe(self.symbols)
            log.info(
                "engine running: symbols=%s strategies=%s execute=%s qty=%s",
                self.symbols,
                [st.name for st in self.strategies],
                self.execute,
                self.default_qty,
            )
            self.started_at, self.running = utcnow(), True
            self._publish_state()

            tasks.append(asyncio.create_task(self._consume_data()))
            if self.broker:
                tasks.append(asyncio.create_task(self._consume_order_updates()))
            if self.bus:
                tasks.append(asyncio.create_task(self._heartbeat()))
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.running = False
            if self.bus and self.started_at is not None:
                if self.broker:
                    await self._refresh_broker_state()  # final snapshot: the state the run ended in
                self._publish_state()
            try:
                await self.data.close()
            finally:
                if self.broker:
                    await self.broker.close()
            log.info("engine stopped")

    def stop(self) -> None:
        self._stop.set()

    async def kill_switch(self) -> None:
        """Halt risk, cancel open orders, stop the loop. Positions are NOT flattened."""
        log.warning("KILL SWITCH: halting risk, cancelling open orders, stopping")
        if self.risk:
            self.risk.halt("kill switch")
        self._publish_state()
        try:
            if self.broker:
                await self.broker.cancel_all()
        finally:
            self.stop()

    # ------------------------------------------------------------------ operator controls
    @property
    def halted(self) -> bool:
        return bool(self.risk and self.risk.halted)

    def halt(self, reason: str = "halted by operator") -> None:
        """Stop opening or increasing positions. Orders that reduce a position still go through."""
        if self.risk is None:
            raise ActionRefused("there is no broker, so there is nothing to halt")
        self.risk.halt(reason)
        log.warning("HALT: %s", reason)
        self._publish_state()

    def resume(self) -> None:
        if self.risk is None:
            raise ActionRefused("there is no broker, so there is nothing to resume")
        self.risk.resume()
        log.warning("RESUME: trading halt cleared")
        self._publish_state()

    @property
    def open_orders(self) -> list[Order]:
        """Orders submitted by this engine that have not reached a terminal state."""
        return list(self._open_orders.values())

    async def flatten(self, symbol: str | None = None) -> list[tuple[str, Order | None, str]]:
        """Close one position, or all of them, at market. Works while halted (exits always pass risk).

        Returns one ``(symbol, order, detail)`` per position looked at; ``order`` is None when
        nothing was sent (already flat, an exit already in flight, or the broker refused).
        """
        if self.broker is None:
            raise ActionRefused("there is no broker, so there are no positions to close")
        results: list[tuple[str, Order | None, str]] = []
        async with self._order_lock:
            await self._refresh_broker_state()
            await self._reconcile_open_orders()
            targets = [p for p in self._positions.values() if symbol is None or p.symbol == symbol]
            for pos in targets:
                remaining = pos.qty + self._pending_qty(pos.symbol)
                if abs(remaining) < 1e-9 or (remaining > 0) != (pos.qty > 0):
                    results.append((pos.symbol, None, "an order that closes it is already in flight"))
                    continue
                req = OrderRequest(
                    symbol=pos.symbol,
                    side=Side.SELL if remaining > 0 else Side.BUY,
                    qty=abs(remaining),
                    note="flatten",
                )
                order, _, detail = await self._submit(req, None)
                results.append((pos.symbol, order, detail))
        if order_sent := [r for r in results if r[1] is not None]:
            log.warning("FLATTEN: %d order(s) sent", len(order_sent))
        return results

    async def submit_manual(self, req: OrderRequest) -> tuple[Order | None, OutcomeKind, str]:
        """Send an operator's order. It takes the same path as a strategy's: risk first."""
        if self.broker is None:
            raise ActionRefused("there is no broker to send an order to")
        if not req.note:
            req.note = "manual"
        async with self._order_lock:
            await self._refresh_broker_state()
            await self._reconcile_open_orders()
            return await self._submit(req, None)

    async def preview_order(self, req: OrderRequest) -> dict[str, object]:
        """What risk would say to this order right now, without sending it or changing any state."""
        if self.broker is None:
            raise ActionRefused("there is no broker to send an order to")
        await self._refresh_broker_state()
        tick = self.last_tick.get(req.symbol)
        positions = self._positions_with_pending()
        ok, why = (True, "ok")
        if self.risk:
            ok, why = self.risk.check(req, tick, positions, self._account, commit=False)
        cur = next((p.qty for p in positions if p.symbol == req.symbol), 0.0)
        price = req.limit_price or (tick.price if tick else None)
        return {
            "ok": ok,
            "why": why,
            "price": price,
            "notional": req.qty * price if price is not None else None,
            "position_before": cur,
            "position_after": cur + (req.qty if req.side == Side.BUY else -req.qty),
        }

    def set_risk_limits(self, **changes: float) -> dict[str, tuple[float, float]]:
        """Change risk limits for the rest of this run. Returns ``{name: (old, new)}`` for what changed."""
        if self.risk is None:
            raise ActionRefused("there is no broker, so no risk limits apply")
        old = self.risk.limits
        new = replace(old, **changes)
        changed = {k: (getattr(old, k), getattr(new, k)) for k in changes if getattr(old, k) != getattr(new, k)}
        self.risk.limits = new
        if changed:
            log.warning("RISK LIMITS changed: %s", ", ".join(f"{k} {a:g} -> {b:g}" for k, (a, b) in changed.items()))
            self._publish_state()
        return changed

    # --- strategies
    def _register(self, st: Strategy) -> str:
        n = self._strategy_seq.get(st.name, 0) + 1
        self._strategy_seq[st.name] = n
        sid = f"{st.name}-{n}"
        self._strategy_ids[sid] = st
        return sid

    def strategy_id(self, st: Strategy) -> str:
        return next(sid for sid, s in self._strategy_ids.items() if s is st)

    def _is_paused(self, st: Strategy) -> bool:
        return bool(self._paused) and any(s is st for sid, s in self._strategy_ids.items() if sid in self._paused)

    def strategy_states(self) -> list[tuple[str, Strategy, bool]]:
        """``(id, strategy, paused)`` for every running strategy, in start order."""
        return [(sid, st, sid in self._paused) for sid, st in self._strategy_ids.items()]

    def _strategy(self, sid: str) -> Strategy:
        try:
            return self._strategy_ids[sid]
        except KeyError:
            raise ActionRefused(f"no strategy '{sid}' is running") from None

    def pause_strategy(self, sid: str) -> None:
        """Ignore a strategy's signals. It keeps receiving bars, so its indicators stay current."""
        self._strategy(sid)
        self._paused.add(sid)
        log.warning("STRATEGY %s paused", sid)
        self._publish_state()

    def resume_strategy(self, sid: str) -> None:
        self._strategy(sid)
        self._paused.discard(sid)
        log.warning("STRATEGY %s resumed", sid)
        self._publish_state()

    async def add_strategy(self, st: Strategy, warmup: bool = True) -> str:
        """Start a strategy on symbols the engine already receives. Returns its id."""
        unknown = [s for s in st.symbols if s not in self.symbols]
        if unknown:
            raise ActionRefused(
                f"{', '.join(unknown)} is not in this session's symbols ({', '.join(self.symbols)}); "
                "restart the engine with it in --symbols"
            )
        if not st.symbols:
            raise ActionRefused("a strategy needs at least one symbol")
        if warmup:
            await self._warmup_one(st)
        sid = self._register(st)
        self.strategies.append(st)
        log.warning("STRATEGY %s added: symbols=%s timeframe=%s", sid, st.symbols, st.timeframe.value)
        self._publish_state()
        return sid

    def remove_strategy(self, sid: str) -> None:
        """Stop a strategy. Its positions stay open; nothing is sold."""
        st = self._strategy(sid)
        self.strategies[:] = [s for s in self.strategies if s is not st]
        del self._strategy_ids[sid]
        self._paused.discard(sid)
        log.warning("STRATEGY %s removed", sid)
        self._publish_state()

    async def cancel_order(self, order_id: str) -> None:
        if self.broker is None:
            raise ActionRefused("there is no broker, so there are no orders to cancel")
        await self.broker.cancel_order(order_id)
        log.warning("CANCEL requested for order %s", order_id)

    # ------------------------------------------------------------------ observability
    @property
    def positions(self) -> list[Position]:
        """Positions as of the last broker refresh."""
        return list(self._positions.values())

    @property
    def account(self) -> Account | None:
        """Account as of the last broker refresh."""
        return self._account

    @property
    def late_events_dropped(self) -> int:
        """Ticks and bars ignored because their bar had already closed, over all aggregators."""
        return sum(a.dropped_late for a in self._aggs.values())

    def _publish(self, ev: Event) -> None:
        if self.bus:
            self.bus.publish(ev)

    def _publish_state(self) -> None:
        if not self.bus:
            return
        if self.broker:
            self.bus.publish(SnapshotEvent(self._account, tuple(self._positions.values())))
        self.bus.publish(
            StatusEvent(
                "running" if self.running else "stopped",
                halted=bool(self.risk and self.risk.halted),
                halt_reason=self.risk.halt_reason if self.risk else "",
            )
        )

    async def _heartbeat(self) -> None:
        """Periodic account snapshot + status, so observers see equity move and know we are alive."""
        while True:
            await asyncio.sleep(self.snapshot_interval)
            try:
                if self.broker:
                    await self._refresh_broker_state()
                self._publish_state()
            except Exception:  # noqa: BLE001 - observability must never stop the engine
                log.exception("heartbeat failed")

    # ------------------------------------------------------------------ warm-up
    async def _warmup(self) -> None:
        for st in self.strategies:
            await self._warmup_one(st)

    async def _warmup_one(self, st: Strategy) -> None:
        if st.warmup_bars <= 0:
            return
        start = utcnow() - timedelta(seconds=st.timeframe.seconds * st.warmup_bars * 3)
        for sym in st.symbols:
            try:
                bars = await self.data.get_bars(sym, st.timeframe, start)
            except Exception as e:  # noqa: BLE001
                log.warning("warmup %s/%s failed: %s", st.name, sym, e)
                continue
            for b in bars[-st.warmup_bars :]:
                st.on_bar(b)  # signals during warm-up are discarded
            if len(bars) < st.warmup_bars:
                log.warning(
                    "warmup %s/%s: only %d of %d bars available; strategy starts cold",
                    st.name,
                    sym,
                    len(bars),
                    st.warmup_bars,
                )
            else:
                log.info("warmup %s/%s: %d bars", st.name, sym, len(bars))

    # ------------------------------------------------------------------ data loop
    async def _consume_data(self) -> None:
        try:
            async for ev in self.data.stream():
                try:
                    if isinstance(ev, Tick):
                        await self._on_tick(ev)
                    elif isinstance(ev, Bar):
                        await self._on_bar(ev)
                except Exception:  # noqa: BLE001
                    log.exception("error handling %s", ev)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("data stream failed; stopping engine")
        finally:
            self.stop()  # end of stream or failure: without data there is nothing to do

    def _agg(self, symbol: str, tf: Timeframe) -> BarAggregator:
        key = (symbol, tf)
        if key not in self._aggs:
            self._aggs[key] = BarAggregator(symbol, tf)
        return self._aggs[key]

    async def _on_tick(self, tick: Tick) -> None:
        self.last_tick[tick.symbol] = tick
        if self.on_tick:
            self.on_tick(tick)
        if self.bus:
            now = time.monotonic()
            if now - self._quote_sent.get(tick.symbol, float("-inf")) >= self.quote_interval:
                self._quote_sent[tick.symbol] = now
                self.bus.publish(QuoteEvent(tick))
        strategies = [st for st in self.strategies if tick.symbol in st.symbols]
        for st in strategies:
            sig = st.on_tick(tick)
            if sig and not self._is_paused(st):
                await self._emit(sig, st.timeframe)
        if self._ticks_build_bars and tick.symbol not in self._bar_symbols:
            # One aggregator per (symbol, timeframe), fed once per event; its completed bar goes to
            # every strategy on that timeframe.
            for tf in dict.fromkeys(st.timeframe for st in strategies):
                done = self._agg(tick.symbol, tf).on_tick(tick)
                if done:
                    await self._deliver(done)

    async def _on_bar(self, bar: Bar) -> None:
        if bar.symbol not in self._bar_symbols:
            self._bar_symbols.add(bar.symbol)
            if self._ticks_build_bars:
                log.debug("%s: provider pushes bars; ticks no longer build bars for it", bar.symbol)
        timeframes = dict.fromkeys(st.timeframe for st in self.strategies if bar.symbol in st.symbols)
        for tf in timeframes:
            done = self._agg(bar.symbol, tf).on_bar(bar)
            if done:
                await self._deliver(done)

    async def _deliver(self, bar: Bar) -> None:
        """Hand a completed bar to every strategy that trades its symbol on its timeframe."""
        self._publish(BarEvent(bar))
        for st in list(self.strategies):  # a copy: the console may add or remove strategies meanwhile
            if bar.symbol not in st.symbols or st.timeframe != bar.timeframe:
                continue
            sig = st.on_bar(bar)  # paused strategies keep their indicators current
            if sig and not self._is_paused(st):
                await self._emit(sig, st.timeframe)

    # ------------------------------------------------------------------ signals
    async def _emit(self, sig: Signal, timeframe: Timeframe | None = None) -> None:
        signal_id = uuid4().hex
        self.signals.append(sig)
        del self.signals[:-MAX_HISTORY]
        self._publish(SignalEvent(signal_id, sig, timeframe.value if timeframe else ""))
        log.info("SIGNAL %s %s %s strength=%.2f %s", sig.strategy, sig.symbol, sig.kind.value, sig.strength, sig.reason)
        if self.on_signal:
            r = self.on_signal(sig)
            if asyncio.iscoroutine(r):
                await r
        if self.execute:
            await self._act(sig, signal_id)

    async def _act(self, sig: Signal, signal_id: str | None = None) -> None:
        assert self.broker is not None
        async with self._order_lock:
            await self._refresh_broker_state()
            await self._reconcile_open_orders()
            cur = self._positions.get(sig.symbol)
            # Orders in flight count as if they had filled: a second signal before the first order
            # fills must not be sized from the stale position (it would duplicate the order).
            cur_qty = (cur.qty if cur else 0.0) + self._pending_qty(sig.symbol)
            target = {
                SignalKind.LONG: self.default_qty * sig.strength,
                SignalKind.SHORT: -self.default_qty * sig.strength,
                SignalKind.FLAT: 0.0,
            }[sig.kind]
            delta = round(target - cur_qty, 4)
            if abs(delta) < 1e-9:
                log.info("no-op: %s already at %.2f", sig.symbol, cur_qty)
                self._publish(SignalOutcome(signal_id, OutcomeKind.NO_OP, f"already at {cur_qty:g}"))
                return
            req = OrderRequest(
                symbol=sig.symbol,
                side=Side.BUY if delta > 0 else Side.SELL,
                qty=abs(delta),
                note=f"{sig.strategy}:{sig.kind.value}",
            )
            await self._submit(req, signal_id)

    def _pending_qty(self, symbol: str) -> float:
        """Signed quantity still to fill on this symbol's open orders."""
        return sum(
            (o.qty - o.filled_qty) * (1 if o.side == Side.BUY else -1)
            for o in self._open_orders.values()
            if o.symbol == symbol
        )

    def _positions_with_pending(self) -> list[Position]:
        """Positions as they will be once every open order has filled: what risk must judge."""
        out = {p.symbol: p for p in self._positions.values()}
        for symbol in {o.symbol for o in self._open_orders.values()}:
            pending = self._pending_qty(symbol)
            cur = out.get(symbol)
            qty = (cur.qty if cur else 0.0) + pending
            if abs(qty) < 1e-9:
                out.pop(symbol, None)
            elif cur:
                out[symbol] = replace(cur, qty=qty)
            else:
                out[symbol] = Position(symbol, qty, 0.0)
        return list(out.values())

    async def _reconcile_open_orders(self) -> None:
        """Ask the broker about open orders we have not heard of for a while (a lost push update
        must not block trading for ever)."""
        assert self.broker is not None
        now = utcnow()
        for oid, o in list(self._open_orders.items()):
            age = (now - o.ts).total_seconds()
            if age < OPEN_ORDER_RECHECK_SEC:
                continue
            try:
                fresh = await self.broker.get_order(oid)
            except Exception as e:  # noqa: BLE001
                if age > OPEN_ORDER_FORGET_SEC:
                    log.warning("forgetting open order %s after %.0fs: %s", oid, age, e)
                    self._open_orders.pop(oid, None)
                continue
            self._track(fresh)

    def _track(self, order: Order) -> None:
        """Remember an order while it is in flight; forget it once it is terminal."""
        if order.status.is_terminal:
            self._open_orders.pop(order.id, None)
        elif order.id in self._open_orders or order.source == getattr(self.broker, "name", ""):
            prev = self._open_orders.get(order.id)
            # keep our own submit time: recheck/forget ages are measured from it
            self._open_orders[order.id] = replace(order, ts=prev.ts) if prev else order

    async def _submit(self, req: OrderRequest, signal_id: str | None) -> tuple[Order | None, OutcomeKind, str]:
        """The one path every order takes: risk check, submit, track, publish.

        Callers hold ``_order_lock`` and have refreshed the broker state. Returns the order (None
        when nothing was sent), what happened, and a human-readable detail.
        """
        assert self.broker is not None
        what = f"{req.side.value} {req.qty:g}"
        if self.risk:
            ok, why = self.risk.check(
                req, self.last_tick.get(req.symbol), self._positions_with_pending(), self._account
            )
            if not ok:
                log.warning("RISK REJECT %s %s %s: %s", req.side.value, req.qty, req.symbol, why)
                self._publish(SignalOutcome(signal_id, OutcomeKind.RISK_REJECT, f"{what}: {why}"))
                if self.risk.halted:
                    self._publish_state()
                return None, OutcomeKind.RISK_REJECT, why
        try:
            order = await self.broker.submit_order(req)
        except Exception as e:  # noqa: BLE001
            log.exception("submit failed for %s", req)
            self._publish(SignalOutcome(signal_id, OutcomeKind.SUBMIT_FAILED, f"{what}: {e}", req.client_order_id))
            return None, OutcomeKind.SUBMIT_FAILED, str(e)
        self.orders.append(order)
        del self.orders[:-MAX_HISTORY]
        if not order.status.is_terminal:
            self._open_orders[order.id] = order
        self._publish(SignalOutcome(signal_id, OutcomeKind.ORDER, what, req.client_order_id))
        self._publish(OrderEvent(order, origin=req.note, signal_id=signal_id))
        log.info(
            "ORDER %s %s %.4f %s -> %s (%s)",
            order.source,
            order.side.value,
            order.qty,
            order.symbol,
            order.status.value,
            order.id,
        )
        return order, OutcomeKind.ORDER, what

    async def _refresh_broker_state(self) -> None:
        assert self.broker is not None
        try:
            self._positions = {p.symbol: p for p in await self.broker.get_positions()}
            self._account = await self.broker.get_account()
        except Exception as e:  # noqa: BLE001
            log.warning("broker state refresh failed: %s", e)

    async def _consume_order_updates(self) -> None:
        assert self.broker is not None
        try:
            async for o in self.broker.order_updates():
                log.info(
                    "ORDER UPDATE %s %s %s filled=%.4f @ %s",
                    o.symbol,
                    o.side.value,
                    o.status.value,
                    o.filled_qty,
                    o.avg_fill_price,
                )
                self._publish(OrderEvent(o, update=True))
                if o.id in self._open_orders:
                    self._track(o)
                if o.status.is_terminal:
                    await self._refresh_broker_state()
                    self._publish_state()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Positions are re-read before every order anyway, so trading can continue.
            log.exception("order update stream failed; continuing without push updates")
