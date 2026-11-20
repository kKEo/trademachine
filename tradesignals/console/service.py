"""ConsoleService: the only object the web layer talks to.

Reads come from the store (history) and from the engine's public state (live); live updates are
fanned out to browser connections as small named notifications. The service never touches a
broker or a provider: anything that changes state goes through ``SignalEngine`` methods.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import analytics
from ..engine import ActionRefused, SignalEngine
from ..events import (
    BarEvent,
    Event,
    EventBus,
    OrderEvent,
    QuoteEvent,
    SignalEvent,
    SignalOutcome,
    SnapshotEvent,
    StatusEvent,
)
from ..models import Bar, OrderRequest, OrderType, Side, Tick, Timeframe, utcnow
from ..store import Store, StoreWriter, bar_from_row, to_epoch
from ..strategies import REGISTRY, Strategy

log = logging.getLogger("tradesignals.console")

#: notification name per persisted event type (sent only after the row is in the database)
_PERSISTED = {
    SignalEvent: "signal",
    SignalOutcome: "signal",
    OrderEvent: "order",
    BarEvent: "bar",
    SnapshotEvent: "snapshot",
}


#: Settings that are safe to show as they are.
PLAIN_SETTINGS = (
    "ALPACA_PAPER",
    "ALPACA_FEED",
    "XTB_DEMO",
    "XTB_SYMBOL_SUFFIX",
    "RISK_MAX_ORDER_NOTIONAL",
    "RISK_MAX_POSITION_QTY",
    "RISK_MAX_OPEN_POSITIONS",
    "RISK_MAX_DAILY_LOSS",
    "RISK_MAX_QUOTE_AGE_SEC",
    "TRADESIGNALS_DB",
    "CONSOLE_PORT",
)
#: Secrets: the console only ever says whether they are set.
SECRET_SETTINGS = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "XTB_USER_ID", "XTB_PASSWORD", "CONSOLE_TOKEN")


class LogBuffer(logging.Handler):
    """The last few hundred ``tradesignals.*`` log records, for the System page.

    Only this project's loggers are captured. Third-party loggers (websockets can log a login
    frame at DEBUG) never reach this handler.
    """

    def __init__(self, capacity: int = 500):
        super().__init__(level=logging.INFO)
        self.records: deque[dict[str, Any]] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name.removeprefix("tradesignals."),
                    "message": record.getMessage(),
                }
            )
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


class Notification:
    """One server-sent event: a name the page reacts to and a small JSON payload."""

    __slots__ = ("name", "data")

    def __init__(self, name: str, data: dict[str, Any] | None = None):
        self.name = name
        self.data = data or {}


class ActionError(Exception):
    """An action the console will not carry out; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


CONFIRM_FLATTEN_ALL = "FLATTEN"
CONFIRM_KILL = "STOP"

#: Risk limits the console may set: name -> (label, smallest, largest, whole number?)
LIMIT_BOUNDS: dict[str, tuple[str, float, float, bool]] = {
    "max_daily_loss": ("Loss since the session started", 1, 1_000_000, False),
    "max_open_positions": ("Open positions", 1, 100, True),
    "max_position_qty": ("Largest position, in shares", 0.0001, 1_000_000, False),
    "max_order_notional": ("Value of a single order", 1, 1_000_000, False),
    "max_quote_age_sec": ("Age of the last quote, in seconds", 1, 3600, False),
}
MAX_ORDER_QTY = 1_000_000


def strategy_catalog() -> list[dict[str, Any]]:
    """Registered strategies and the parameters their constructors take (besides symbols and timeframe)."""
    out = []
    for name, cls in sorted(REGISTRY.items()):
        params = []
        default_tf = Timeframe.M1
        for p in list(inspect.signature(cls.__init__).parameters.values())[2:]:
            if p.name == "timeframe":
                default_tf = p.default
            elif isinstance(p.default, (int, float)) and not isinstance(p.default, bool):
                params.append({"name": p.name, "default": p.default, "whole": isinstance(p.default, int)})
        out.append(
            {
                "name": name,
                "about": (inspect.getdoc(cls) or "").split("\n")[0],
                "params": params,
                "timeframe": default_tf.value,
            }
        )
    return out


def strategy_params(st: Strategy) -> dict[str, Any]:
    """Current values of a running strategy's constructor parameters, where it exposes them."""
    names = [p["name"] for c in strategy_catalog() if c["name"] == st.name for p in c["params"]]
    return {n: getattr(st, n) for n in names if hasattr(st, n)}


class ConsoleService:
    def __init__(self, engine: SignalEngine, store: Store, bus: EventBus, run_id: str = ""):
        self.engine = engine
        self.store = store
        self.bus = bus
        self.run_id = run_id
        self.closing = asyncio.Event()
        self._clients: set[asyncio.Queue[Notification]] = set()
        self._sub = bus.subscribe(maxsize=2000, types=(QuoteEvent, StatusEvent), name="console")
        self._task: asyncio.Task | None = None
        self._day_open: dict[str, float] = {}  # first price seen per symbol, for the change column
        self._backfilled: dict[tuple[str, Timeframe], float] = {}  # (symbol, tf) -> monotonic time of last fetch
        self._backfill_locks: dict[tuple[str, Timeframe], asyncio.Lock] = {}
        self._writer: StoreWriter | None = None
        self.logs = LogBuffer()

    # ------------------------------------------------------------------ lifecycle
    def start(self, writer: StoreWriter | None = None) -> None:
        """Begin forwarding live events. ``writer`` announces rows once they are stored."""
        if writer is not None:
            self._writer = writer
            writer.listeners.append(self._on_persisted)
        logging.getLogger("tradesignals").addHandler(self.logs)
        self._task = asyncio.create_task(self._pump(), name="console-pump")

    async def stop(self) -> None:
        self.closing.set()
        logging.getLogger("tradesignals").removeHandler(self.logs)
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._sub.close()
        self._broadcast(Notification("bye"))

    async def _pump(self) -> None:
        async for ev in self._sub:
            if isinstance(ev, QuoteEvent):
                self._broadcast(Notification("quote", self._quote(ev.tick)))
            elif isinstance(ev, StatusEvent):
                self._broadcast(Notification("status"))

    def _on_persisted(self, batch: Sequence[Event]) -> None:
        names: dict[str, None] = {}
        for ev in batch:
            name = _PERSISTED.get(type(ev))
            if name == "bar":
                self._broadcast(Notification("bar", bar_json(ev.bar)))  # type: ignore[union-attr]
            elif name:
                names[name] = None
        for name in names:  # one notification per kind and batch, however many rows it held
            self._broadcast(Notification(name))

    # ------------------------------------------------------------------ fan-out
    def connect(self) -> asyncio.Queue[Notification]:
        q: asyncio.Queue[Notification] = asyncio.Queue(maxsize=500)
        self._clients.add(q)
        return q

    def disconnect(self, q: asyncio.Queue[Notification]) -> None:
        self._clients.discard(q)

    def _broadcast(self, n: Notification) -> None:
        for q in tuple(self._clients):
            if q.full():
                q.get_nowait()  # a stalled browser tab loses old notifications, never blocks us
            q.put_nowait(n)

    # ------------------------------------------------------------------ live state
    def mode(self) -> str:
        e = self.engine
        if not e.execute or e.broker is None:
            return "signal-only"
        return "paper" if e.broker.paper else "live"

    def status(self) -> dict[str, Any]:
        e = self.engine
        now = utcnow()
        return {
            "mode": self.mode(),
            "running": e.running,
            "data": e.data.name,
            "broker": e.broker.name if e.broker else None,
            "execute": e.execute,
            "started_at": to_epoch(e.started_at) if e.started_at else None,
            "uptime_sec": (now - e.started_at).total_seconds() if e.started_at else None,
            "halted": bool(e.risk and e.risk.halted),
            "halt_reason": e.risk.halt_reason if e.risk else "",
            "symbols": list(e.symbols),
            "strategies": [
                {"id": sid, "name": st.name, "timeframe": st.timeframe.value, "symbols": list(st.symbols), "paused": p}
                for sid, st, p in e.strategy_states()
            ],
            "default_qty": e.default_qty,
            "run_id": self.run_id,
            "now": to_epoch(now),
        }

    def _quote(self, t: Tick) -> dict[str, Any]:
        first = self._day_open.setdefault(t.symbol, t.price)
        return {
            "symbol": t.symbol,
            "price": t.price,
            "bid": t.bid,
            "ask": t.ask,
            "ts": to_epoch(t.ts),
            "age_sec": max(0.0, (utcnow() - t.ts).total_seconds()),
            "change_pct": (t.price / first - 1) * 100 if first else 0.0,
        }

    def quotes(self) -> list[dict[str, Any]]:
        ticks = self.engine.last_tick
        return [self._quote(ticks[s]) if s in ticks else {"symbol": s, "price": None} for s in self.engine.symbols]

    def positions(self) -> list[dict[str, Any]]:
        ticks = self.engine.last_tick
        rows = []
        for p in self.engine.positions:
            last = ticks[p.symbol].price if p.symbol in ticks else None
            value = p.market_value if p.market_value is not None else (last * p.qty if last is not None else None)
            pnl = p.unrealized_pnl
            if pnl is None and last is not None:
                pnl = (last - p.avg_price) * p.qty
            cost = abs(p.avg_price * p.qty)
            rows.append(
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "last": last,
                    "market_value": value,
                    "unrealized_pnl": pnl,
                    "unrealized_pct": (pnl / cost * 100) if pnl is not None and cost else None,
                    "traded": p.symbol in self.engine.symbols,
                }
            )
        gross = sum(abs(r["market_value"] or 0.0) for r in rows)
        for r in rows:
            r["share_pct"] = abs(r["market_value"] or 0.0) / gross * 100 if gross else 0.0
        return sorted(rows, key=lambda r: -abs(r["market_value"] or 0.0))

    # ------------------------------------------------------------------ history (store, off the loop)
    async def _db(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def kpis(self) -> dict[str, Any]:
        account = self.engine.account
        positions = self.positions()
        day_start = to_epoch(utcnow().replace(hour=0, minute=0, second=0, microsecond=0))
        day_open, peak = await asyncio.gather(
            self._db(self.store.equity_first, day_start),
            self._db(self.store.equity_peak, self.run_id or None),
        )
        equity = account.equity if account else None
        day_pnl = equity - day_open if equity is not None and day_open is not None else None
        if equity is not None and peak is not None:
            peak = max(peak, equity)
        return {
            "has_account": account is not None,
            "equity": equity,
            "cash": account.cash if account else None,
            "buying_power": account.buying_power if account else None,
            "currency": account.currency if account else "",
            "day_pnl": day_pnl,
            "day_pnl_pct": day_pnl / day_open * 100 if day_pnl is not None and day_open else None,
            "drawdown_pct": (equity / peak - 1) * 100 if equity is not None and peak else None,
            "open_positions": len(positions),
            "unrealized_pnl": sum(p["unrealized_pnl"] or 0.0 for p in positions) if positions else 0.0,
            "exposure": sum(abs(p["market_value"] or 0.0) for p in positions),
        }

    async def signals(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._db(self.store.signals, **filters)

    async def orders(self, **filters: Any) -> list[dict[str, Any]]:
        return await self._db(self.store.orders, **filters)

    async def equity(self, since: float | None = None) -> list[dict[str, Any]]:
        rows = await self._db(self.store.equity, since=since, limit=200_000)
        return _thin([{"time": int(r["ts"]), "value": r["equity"]} for r in _dedupe_seconds(rows)])

    async def runs(self) -> list[dict[str, Any]]:
        return await self._db(self.store.runs)

    # ------------------------------------------------------------------ actions
    def capabilities(self) -> dict[str, Any]:
        """What this session may do from the console, and why not when it may not."""
        e = self.engine
        blocked = None
        if e.broker is None:
            blocked = "No broker is connected."
        elif not e.execute:
            blocked = "The engine was started without --execute, so the console sends no orders either."
        elif not e.broker.paper:
            blocked = "This is a live account. In this phase the console trades paper and demo accounts only."
        return {
            "has_broker": e.broker is not None,
            "paper": bool(e.broker and e.broker.paper),
            "can_trade": blocked is None,
            "trade_blocked": blocked,
            "can_halt": e.risk is not None,
            "running": e.running,
        }

    def _need_trading(self) -> None:
        blocked = self.capabilities()["trade_blocked"]
        if blocked:
            raise ActionError(blocked, 403)

    async def _audited(self, action: str, detail: str, fn) -> dict[str, Any]:
        """Run an action and record it, whether it worked, was refused or failed."""
        try:
            message = await fn()
        except ActionError as e:
            await self._db(self.store.audit, action, detail, f"refused: {e.message}", self.run_id)
            raise
        except ActionRefused as e:
            await self._db(self.store.audit, action, detail, f"refused: {e}", self.run_id)
            raise ActionError(_sentence(str(e))) from e
        except Exception as e:  # noqa: BLE001
            log.exception("console action %s failed", action)
            await self._db(self.store.audit, action, detail, f"failed: {e}", self.run_id)
            raise ActionError(f"The action failed: {e}", 502) from e
        await self._db(self.store.audit, action, detail, message, self.run_id)
        self._broadcast(Notification("status"))
        self._broadcast(Notification("audit"))
        return {"ok": True, "message": message}

    async def halt(self, reason: str = "") -> dict[str, Any]:
        reason = (reason or "").strip()[:200] or "halted from the console"

        async def run() -> str:
            self.engine.halt(reason)
            return "Trading halted. Orders that reduce a position still go through."

        return await self._audited("halt", reason, run)

    async def resume(self) -> dict[str, Any]:
        async def run() -> str:
            self._need_trading()
            if not self.engine.halted:
                raise ActionError("Trading is not halted.")
            self.engine.resume()
            return "Trading resumed."

        return await self._audited("resume", "", run)

    async def flatten(self, symbol: str | None = None, confirm: str = "") -> dict[str, Any]:
        symbol = symbol.upper() if symbol else None

        async def run() -> str:
            self._need_trading()
            if symbol is None and confirm != CONFIRM_FLATTEN_ALL:
                raise ActionError(f"Type {CONFIRM_FLATTEN_ALL} to close every position.", 400)
            if symbol is not None and symbol not in {p.symbol for p in self.engine.positions}:
                await self.engine._refresh_broker_state()
                if symbol not in {p.symbol for p in self.engine.positions}:
                    raise ActionError(f"There is no open position in {symbol}.")
            results = await self.engine.flatten(symbol)
            sent = [f"{o.side.value} {o.qty:g} {sym}" for sym, o, _ in results if o is not None]
            skipped = [f"{sym}: {detail}" for sym, o, detail in results if o is None]
            if not results:
                return "There are no open positions."
            parts = []
            if sent:
                parts.append("Sent " + ", ".join(sent) + ".")
            if skipped:
                parts.append("Not sent: " + "; ".join(skipped) + ".")
            return " ".join(parts)

        return await self._audited("flatten", symbol or "all positions", run)

    async def cancel(self, order_id: str) -> dict[str, Any]:
        async def run() -> str:
            self._need_trading()
            if order_id not in {o.id for o in self.engine.open_orders}:
                raise ActionError("That order is not open any more.")
            await self.engine.cancel_order(order_id)
            return f"Cancel requested for order {order_id}."

        return await self._audited("cancel", order_id, run)

    async def kill_switch(self, confirm: str = "") -> dict[str, Any]:
        async def run() -> str:
            if confirm != CONFIRM_KILL:
                raise ActionError(f"Type {CONFIRM_KILL} to stop the engine.", 400)
            await self.engine.kill_switch()
            return "Kill switch: trading halted, open orders cancelled, engine stopping. Positions are left open."

        return await self._audited("kill_switch", "", run)

    # --- manual orders
    def _order_request(self, data: dict[str, Any]) -> OrderRequest:
        symbol = str(data.get("symbol") or "").upper()
        if symbol not in self.known_symbols():
            raise ActionError(f"Choose one of this session's symbols: {', '.join(self.known_symbols())}.", 400)
        side = str(data.get("side") or "").lower()
        if side not in ("buy", "sell"):
            raise ActionError("Choose buy or sell.", 400)
        qty = _number(data.get("qty"), "Shares", 0.0001, MAX_ORDER_QTY)
        kind = str(data.get("type") or "market").lower()
        if kind not in ("market", "limit"):
            raise ActionError("Choose a market or a limit order.", 400)
        limit_price = None
        if kind == "limit":
            limit_price = _number(data.get("limit_price"), "Limit price", 0.0001, 10_000_000)
        return OrderRequest(
            symbol=symbol,
            side=Side(side),
            qty=qty,
            type=OrderType(kind),
            limit_price=limit_price,
            note="manual",
        )

    async def preview_order(self, data: dict[str, Any]) -> dict[str, Any]:
        """Risk's verdict on an order, without sending it. Not audited: nothing happens."""
        self._need_trading()
        req = self._order_request(data)
        try:
            p = await self.engine.preview_order(req)
        except ActionRefused as e:
            raise ActionError(_sentence(str(e))) from e
        what = f"{req.side.value} {req.qty:g} {req.symbol}"
        value = f", about {p['notional']:,.2f}" if p["notional"] is not None else ""
        after = f"The position would go from {p['position_before']:g} to {p['position_after']:g} shares."
        if p["ok"]:
            message = f"Risk would allow {what}{value}. {after}"
        else:
            message = f"Risk would block {what}: {p['why']}."
        return {"ok": True, "allowed": p["ok"], "message": message, **p}

    async def send_order(self, data: dict[str, Any]) -> dict[str, Any]:
        detail = " ".join(str(data.get(k) or "") for k in ("side", "qty", "symbol", "type", "limit_price")).strip()

        async def run() -> str:
            self._need_trading()
            req = self._order_request(data)
            order, kind, why = await self.engine.submit_manual(req)
            what = f"{req.side.value} {req.qty:g} {req.symbol}"
            if order is None:
                reason = "Risk blocked" if kind.value == "risk_reject" else "The broker refused"
                raise ActionError(f"{reason} {what}: {why}.")
            status = order.status.value.replace("_", " ")
            price = f" at {order.avg_fill_price:,.2f}" if order.avg_fill_price is not None else ""
            return f"Order sent: {what}, {status}{price}."

        return await self._audited("order", detail, run)

    # --- risk limits
    async def set_limits(self, data: dict[str, Any]) -> dict[str, Any]:
        detail = ", ".join(f"{k}={data[k]}" for k in LIMIT_BOUNDS if data.get(k) not in (None, ""))

        async def run() -> str:
            self._need_trading()
            if self.engine.risk is None:
                raise ActionError("No risk limits apply without a broker.")
            current = self.engine.risk.limits
            changes: dict[str, float] = {}
            for name, (label, lo, hi, whole) in LIMIT_BOUNDS.items():
                if data.get(name) in (None, ""):
                    continue
                try:
                    if float(str(data[name]).replace(",", "")) == getattr(current, name):
                        continue  # untouched fields are not re-validated (a start value may lie outside)
                except ValueError:
                    pass
                value = _number(data[name], label, lo, hi, whole)
                changes[name] = int(value) if whole else value
            if not changes:
                return "No limit was changed."
            changed = self.engine.set_risk_limits(**changes)
            return (
                "Changed until the engine restarts: "
                + "; ".join(f"{LIMIT_BOUNDS[k][0].lower()} {a:g} to {b:g}" for k, (a, b) in changed.items())
                + "."
            )

        return await self._audited("set_limits", detail, run)

    def limits_view(self) -> list[dict[str, Any]]:
        risk = self.engine.risk
        if risk is None:
            return []
        return [
            {"name": name, "label": label, "value": getattr(risk.limits, name), "min": lo, "max": hi, "whole": whole}
            for name, (label, lo, hi, whole) in LIMIT_BOUNDS.items()
        ]

    # --- strategies
    def strategies_view(self) -> list[dict[str, Any]]:
        return [
            {
                "id": sid,
                "name": st.name,
                "symbols": list(st.symbols),
                "timeframe": st.timeframe.value,
                "params": strategy_params(st),
                "paused": paused,
                "warmup_bars": st.warmup_bars,
            }
            for sid, st, paused in self.engine.strategy_states()
        ]

    async def strategy_action(self, action: str, sid: str) -> dict[str, Any]:
        async def run() -> str:
            self._need_trading()
            if action == "pause":
                self.engine.pause_strategy(sid)
                return f"{sid} paused. It keeps watching the market; its signals are ignored."
            if action == "resume":
                self.engine.resume_strategy(sid)
                return f"{sid} resumed."
            if action == "remove":
                self.engine.remove_strategy(sid)
                return f"{sid} removed. Its positions stay open."
            raise ActionError("Unknown strategy action.", 400)

        return await self._audited(f"strategy_{action}", sid, run)

    async def add_strategy(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("strategy") or "")
        symbols = data.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [s for s in symbols.replace(",", " ").split() if s]
        symbols = [str(s).upper() for s in symbols]
        detail = f"{name} on {', '.join(symbols) or 'no symbols'}"

        async def run() -> str:
            self._need_trading()
            entry = next((c for c in strategy_catalog() if c["name"] == name), None)
            if entry is None:
                raise ActionError(f"Choose one of: {', '.join(sorted(REGISTRY))}.", 400)
            if not symbols:
                raise ActionError("Choose at least one symbol.", 400)
            kwargs: dict[str, Any] = {}
            for p in entry["params"]:
                raw = data.get(p["name"])
                if raw in (None, ""):
                    continue
                value = _number(raw, p["name"], 0.0001, 100_000, p["whole"])
                kwargs[p["name"]] = int(value) if p["whole"] else value
            tf = str(data.get("timeframe") or entry["timeframe"])
            try:
                kwargs["timeframe"] = Timeframe(tf)
            except ValueError:
                raise ActionError(f"Unknown bar size '{tf}'.", 400) from None
            merged = {p["name"]: kwargs.get(p["name"], p["default"]) for p in entry["params"]}
            if "fast" in merged and "slow" in merged and merged["fast"] >= merged["slow"]:
                raise ActionError("The fast average must be shorter than the slow one.", 400)
            try:
                st = REGISTRY[name](symbols, **kwargs)
            except Exception as e:  # noqa: BLE001
                raise ActionError(f"The strategy could not be created: {e}", 400) from e
            sid = await self.engine.add_strategy(st)
            return f"{sid} started on {', '.join(symbols)} with {tf} bars."

        return await self._audited("strategy_add", detail, run)

    def open_orders(self) -> list[dict[str, Any]]:
        return [
            {
                "id": o.id,
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": o.qty,
                "filled_qty": o.filled_qty,
                "status": o.status.value,
                "ts": to_epoch(o.ts),
                "source": o.source,
            }
            for o in sorted(self.engine.open_orders, key=lambda o: o.ts, reverse=True)
        ]

    def risk_view(self) -> dict[str, Any] | None:
        """Limits and how much of each is used right now. None when there is no risk manager."""
        risk = self.engine.risk
        if risk is None:
            return None
        lim, account, positions = risk.limits, self.engine.account, self.engine.positions
        loss = None
        if account is not None and risk.start_equity is not None:
            loss = max(0.0, risk.start_equity - account.equity)
        biggest = max((abs(p.qty) for p in positions), default=0.0)

        def row(name: str, used: float | None, limit: float, kind: str, note: str) -> dict[str, Any]:
            share = min(100.0, used / limit * 100) if used is not None and limit > 0 else None
            return {"name": name, "used": used, "limit": limit, "kind": kind, "share_pct": share, "note": note}

        return {
            "halted": risk.halted,
            "halt_reason": risk.halt_reason,
            "start_equity": risk.start_equity,
            "rows": [
                row(
                    "Loss since the session started",
                    loss,
                    lim.max_daily_loss,
                    "money",
                    "Trading halts when the account has lost this much since the engine started or was resumed.",
                ),
                row(
                    "Open positions",
                    float(len(positions)),
                    float(lim.max_open_positions),
                    "count",
                    "A position in a new symbol is refused once this many are open.",
                ),
                row(
                    "Largest position, in shares",
                    biggest,
                    lim.max_position_qty,
                    "qty",
                    "No order may take a position beyond this size.",
                ),
                row("Value of a single order", None, lim.max_order_notional, "money", "Larger orders are refused."),
                row(
                    "Age of the last quote, in seconds",
                    None,
                    lim.max_quote_age_sec,
                    "qty",
                    "Orders are refused when the latest quote is older than this.",
                ),
            ],
        }

    async def audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._db(self.store.audit_log, limit)

    # ------------------------------------------------------------------ system
    def log_tail(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        """Newest first. ``level="WARNING"`` keeps warnings and errors only."""
        floor = logging.getLevelName(level) if level else logging.INFO
        floor = floor if isinstance(floor, int) else logging.INFO
        rows = [r for r in self.logs.records if logging.getLevelName(r["level"]) >= floor]
        return rows[-limit:][::-1]

    def settings_view(self) -> list[dict[str, Any]]:
        """Configuration as the process sees it. Secrets are reported as set or not set, never shown."""
        rows = [{"name": n, "value": os.environ.get(n, ""), "secret": False} for n in PLAIN_SETTINGS]
        rows += [
            {"name": n, "value": "set" if os.environ.get(n) else "not set", "secret": True} for n in SECRET_SETTINGS
        ]
        return rows

    async def health(self) -> dict[str, Any]:
        counts, size = await asyncio.gather(self._db(self.store.counts), self._db(self.store.size_bytes))
        ticks = self.engine.last_tick
        now = utcnow()
        return {
            "database": {"path": self.store.path, "size_bytes": size, "rows": counts},
            "subscribers": [{"name": n, "dropped": d} for n, d in self.bus.subscribers()],
            "failed_batches": self._writer.failed_batches if self._writer else 0,
            "late_events_dropped": self.engine.late_events_dropped,
            "open_orders": len(self.engine.open_orders),
            "browser_tabs": len(self._clients),
            "quote_age_sec": {
                s: (now - ticks[s].ts).total_seconds() if s in ticks else None for s in self.engine.symbols
            },
        }

    # ------------------------------------------------------------------ signal analysis
    async def analysed_signals(self, limit: int = 2000, **filters: Any) -> list[dict[str, Any]]:
        """Signals (newest first) with what the price did afterwards: ``ref``, ``returns``, ``edge``."""
        rows = await self.signals(limit=limit, **filters)
        groups: dict[tuple[str, str], float] = {}
        for r in rows:
            r["timeframe"] = r.get("timeframe") or self.timeframe_for(r["symbol"]).value
            key = (r["symbol"], r["timeframe"])
            groups[key] = min(groups.get(key, r["ts"]), r["ts"])
        series: dict[tuple[str, str], tuple[list[float], list[float]]] = {}
        for (symbol, tf), first in groups.items():
            bar_rows = await self._db(self.store.bars, symbol, tf, since=first - Timeframe(tf).seconds, limit=1_000_000)
            series[(symbol, tf)] = ([b["start"] for b in bar_rows], [b["close"] for b in bar_rows])
        for r in rows:
            times, closes = series[(r["symbol"], r["timeframe"])]
            r.update(analytics.forward_returns(r["kind"], r["ts"], times, closes))
        return rows

    async def signal_report(self, **filters: Any) -> dict[str, Any]:
        rows = await self.analysed_signals(**filters)
        by_strategy = analytics.signal_stats(rows)
        trips = await self.trips()
        trade_stats = analytics.group_stats(trips, "strategy")
        for name, g in by_strategy.items():
            g["trades"] = trade_stats.get(name)
        outcomes: dict[str, int] = {}
        for r in rows:
            key = r.get("outcome") or "none"
            outcomes[key] = outcomes.get(key, 0) + 1
        overall = analytics.signal_stats([{**r, "strategy": "all"} for r in rows]).get("all")
        return {
            "signals": rows,
            "count": len(rows),
            "outcomes": outcomes,
            "overall": overall,
            "by_strategy": by_strategy,
            "horizons": list(analytics.HORIZONS),
            "facets": await self._db(self.store.signal_facets),
        }

    async def trips(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Round trips built from every stored fill, oldest first."""
        return analytics.round_trips(await self._db(self.store.fills, symbol=symbol))

    async def trade_report(self) -> dict[str, Any]:
        trips = await self.trips()
        return {
            "trips": trips[::-1],
            "stats": analytics.trip_stats(trips),
            "by_symbol": analytics.group_stats(trips, "symbol"),
            "by_strategy": analytics.group_stats(trips, "strategy"),
        }

    async def signal_detail(self, signal_id: str) -> dict[str, Any] | None:
        row = await self._db(self.store.signal, signal_id)
        if row is None:
            return None
        tf = Timeframe(row.get("timeframe") or self.timeframe_for(row["symbol"]).value)
        row["timeframe"] = tf.value
        bar_rows = await self._db(
            self.store.bars, row["symbol"], tf.value, since=row["ts"] - tf.seconds, limit=1_000_000
        )
        row.update(
            analytics.forward_returns(
                row["kind"], row["ts"], [b["start"] for b in bar_rows], [b["close"] for b in bar_rows]
            )
        )
        outcomes = await self._db(self.store.outcomes, signal_id)
        order = updates = quality = None
        if row.get("client_order_id"):
            order = await self._db(self.store.order_by_client_id, row["client_order_id"])
        if order:
            updates = await self._db(self.store.order_updates, order["source"], order["id"])
            quality = analytics.execution_quality(
                order["side"], order["avg_fill_price"], row["ref"], row["ts"], order["ts"], tf.seconds
            )
        trip = None
        for t in await self.trips(row["symbol"]):
            if t["entry_ts"] == row["ts"] or t["exit_ts"] == row["ts"]:
                trip = {**t, "role": "opened" if t["entry_ts"] == row["ts"] else "closed"}
        return {
            "signal": row,
            "outcomes": outcomes,
            "order": order,
            "order_updates": updates or [],
            "quality": quality,
            "trip": trip,
            "horizons": list(analytics.HORIZONS),
        }

    # ------------------------------------------------------------------ markets
    def known_symbols(self) -> list[str]:
        """Traded symbols first, then symbols that are only held."""
        held = [p.symbol for p in self.engine.positions if p.symbol not in self.engine.symbols]
        return [*self.engine.symbols, *sorted(held)]

    def timeframe_for(self, symbol: str) -> Timeframe:
        """The bar size of the first strategy trading the symbol; one minute when none does."""
        for st in self.engine.strategies:
            if symbol in st.symbols:
                return st.timeframe
        return Timeframe.M1

    def _live_timeframes(self, symbol: str) -> list[Timeframe]:
        """Timeframes the engine produces bars in for this symbol, finest first."""
        tfs = {st.timeframe for st in self.engine.strategies if symbol in st.symbols}
        return sorted(tfs, key=lambda tf: tf.seconds)

    async def _backfill(self, symbol: str, tf: Timeframe, want: int) -> str | None:
        """Fetch history from the data provider into the store, at most once per bar period.

        Goes through ``MarketDataProvider.get_bars`` like the engine's warm-up, so provider rate
        limits (the XTB command throttle) are respected. Returns a note when the fetch failed.
        """
        key = (symbol, tf)
        lock = self._backfill_locks.setdefault(key, asyncio.Lock())
        async with lock:
            loop_time = asyncio.get_running_loop().time()
            last = self._backfilled.get(key)
            if last is not None and (
                tf in self._live_timeframes(symbol)  # the engine keeps this timeframe current by itself
                or loop_time - last < max(60.0, tf.seconds)
            ):
                return None
            self._backfilled[key] = loop_time
            start = utcnow() - timedelta(seconds=tf.seconds * want * 3)
            try:
                bars = await self.engine.data.get_bars(symbol, tf, start)
            except Exception as e:  # noqa: BLE001 - a chart must degrade, not fail
                log.warning("history %s/%s failed: %s", symbol, tf.value, e)
                return f"History could not be loaded from {self.engine.data.name}: {e}"
            if bars:
                await self._db(self.store.put_bars, bars)
            return None

    async def bars(
        self, symbol: str, tf: Timeframe, limit: int = 500, before: float | None = None
    ) -> tuple[list[Bar], str | None]:
        """Bars for a chart, oldest first: provider history plus what the engine has produced live.

        When the engine only produces finer bars than asked for (a strategy on 1m, a chart on 15m),
        the finer bars after the last stored coarse bar are rolled up to fill the gap to now.
        """
        note = None
        if before is None:
            note = await self._backfill(symbol, tf, limit)
        until = before - 1e-6 if before is not None else None
        rows = await self._db(self.store.bars, symbol, tf.value, until=until, limit=limit)
        bars = [bar_from_row(r) for r in rows]
        if before is None:
            finer = [t for t in self._live_timeframes(symbol) if t.seconds < tf.seconds]
            if finer:
                since = to_epoch(bars[-1].start) if bars else None
                fine_rows = await self._db(self.store.bars, symbol, finer[0].value, since=since, limit=5000)
                rolled = analytics.resample([bar_from_row(r) for r in fine_rows], tf)
                bars = analytics.merge_bars(bars, rolled)[-limit:]
        return bars, note

    async def chart(
        self,
        symbol: str,
        tf: Timeframe,
        limit: int = 500,
        before: float | None = None,
        sma: Sequence[int] = (10, 30),
        ema: Sequence[int] = (20,),
        rsi: Sequence[int] = (14,),
    ) -> dict[str, Any]:
        bars, note = await self.bars(symbol, tf, limit, before)
        payload: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": tf.value,
            "seconds": tf.seconds,
            "bars": [bar_json(b) for b in bars],
            "indicators": analytics.indicator_series(bars, sma=sma, ema=ema, rsi=rsi),
            "markers": [],
            "note": note,
        }
        if bars:
            since = to_epoch(bars[0].start)
            until = to_epoch(bars[-1].start) + tf.seconds
            signals, fills = await asyncio.gather(
                self._db(self.store.signals, symbol=symbol, since=since, until=until, limit=1000),
                self._db(self.store.fills, symbol=symbol),
            )
            payload["markers"] = chart_markers(signals, fills, tf, until)
        return payload

    async def watchlist(self) -> list[dict[str, Any]]:
        quotes = {q["symbol"]: q for q in self.quotes()}
        held = {p["symbol"]: p for p in self.positions()}
        rows = []
        for symbol in self.known_symbols():
            tf = self.timeframe_for(symbol)
            bars, _ = await self.bars(symbol, tf, limit=120)
            state = analytics.trend_state(bars)
            closes = [b.close for b in bars[-60:]]
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": tf.value,
                    "quote": quotes.get(symbol, {"symbol": symbol, "price": None}),
                    "position": held.get(symbol),
                    "state": state,
                    "spark": analytics.sparkline(closes),
                    "spark_tone": "up" if len(closes) > 1 and closes[-1] >= closes[0] else "down",
                    "strategies": [st.name for st in self.engine.strategies if symbol in st.symbols],
                }
            )
        return rows


def chart_markers(
    signals: list[dict[str, Any]], fills: list[dict[str, Any]], tf: Timeframe, until: float
) -> list[dict[str, Any]]:
    """Signals and fills as chart markers, snapped to the bar they fall into, oldest first."""

    def snap(ts: float) -> int:
        return int(ts - ts % tf.seconds)

    out: list[dict[str, Any]] = []
    for s in signals:
        out.append(
            {
                "time": snap(s["ts"]),
                "type": "signal",
                "kind": s["kind"],
                "text": s["kind"],
                "detail": f"{s['strategy']}: {s['reason']}".strip(": "),
                "id": s["id"],
            }
        )
    for o in fills:
        # Market time, not wall-clock time: a fill belongs on the bar of the signal that caused it.
        # Live the two are seconds apart; in a replay the order is stamped "now", far off the chart.
        ts = o.get("signal_ts") or o["ts"]
        if ts > until:
            continue
        price = f" @ {o['avg_fill_price']:.2f}" if o["avg_fill_price"] is not None else ""
        out.append(
            {
                "time": snap(ts),
                "type": "fill",
                "kind": o["side"],
                "text": f"{o['side']} {o['filled_qty']:g}",
                "detail": f"{o['side']} {o['filled_qty']:g}{price}",
                "id": o["id"],
            }
        )
    return sorted(out, key=lambda m: (m["time"], m["type"]))


def _sentence(text: str) -> str:
    """Upper-case the first letter only (str.capitalize would lower-case symbols) and end with a full stop."""
    text = text[:1].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def _number(raw: Any, label: str, lo: float, hi: float, whole: bool = False) -> float:
    """Parse and range-check a number typed into a form; the error names the field."""
    try:
        value = float(str(raw).replace(",", ""))
    except ValueError:
        raise ActionError(f"{label} must be a number.", 400) from None
    if value != value or not lo <= value <= hi:  # NaN or out of range
        raise ActionError(f"{label} must be between {lo:g} and {hi:g}.", 400)
    if whole and value != int(value):
        raise ActionError(f"{label} must be a whole number.", 400)
    return value


def bar_json(bar) -> dict[str, Any]:
    return {
        "symbol": bar.symbol,
        "timeframe": bar.timeframe.value,
        "time": int(to_epoch(bar.start)),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


MAX_CHART_POINTS = 2000


def _thin(points: list[dict[str, Any]], limit: int = MAX_CHART_POINTS) -> list[dict[str, Any]]:
    """At most ``limit`` points for a line chart: the last point of each equal slice, plus the very last."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[min(len(points) - 1, int((i + 1) * step) - 1)] for i in range(limit)]
    out[-1] = points[-1]
    return out


def _dedupe_seconds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Charts need strictly increasing whole-second timestamps: keep the last row of each second."""
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        out[int(r["ts"])] = r
    return [out[k] for k in sorted(out)]


def as_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None
