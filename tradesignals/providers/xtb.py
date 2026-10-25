"""XTB xAPI adapter. Implements the JSON-over-WebSocket protocol directly (no SDK).

Protocol notes (xAPI; the official documentation host developers.xstore.pro no longer resolves,
third-party copies of the spec exist, e.g. the xapi-python project):
- Main socket  wss://ws.xtb.com/{demo|real}        request/response, max 1 req / 200 ms
- Stream socket wss://ws.xtb.com/{demo|real}Stream  push; subscribe with streamSessionId
- Prices in getChartLastRequest are scaled by `digits`; high/low/close are deltas from open.
- Positions are per-trade. Selling a real stock = CLOSE of an existing position (type=2),
  not a new SELL trade. CFDs allow a fresh SELL (short).
- tradeTransaction `type`: 0 OPEN, 1 PENDING, 2 CLOSE, 3 MODIFY, 4 DELETE.
- tradeTransactionStatus `requestStatus`: 0 ERROR, 1 PENDING, 3 ACCEPTED, 4 REJECTED. The reply
  carries no fill price (fields: ask, bid, customComment, message, order, requestStatus), so
  market fills are reported at the price we sent.

Env: XTB_USER_ID, XTB_PASSWORD, XTB_DEMO=true|false, XTB_SYMBOL_SUFFIX=.US|.US_9
Verify the stock symbol suffix on your account with `getAllSymbols` (category "STC").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
from typing import Any

from ..config import env_bool
from ..interfaces import Broker, MarketDataProvider, SymbolMapper
from ..models import (
    Account,
    Bar,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Tick,
    Timeframe,
)

log = logging.getLogger("tradesignals.xtb")

_PERIOD = {Timeframe.M1: 1, Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.H1: 60, Timeframe.D1: 1440}
_CMD_BUY, _CMD_SELL, _CMD_BUY_LIMIT, _CMD_SELL_LIMIT, _CMD_BUY_STOP, _CMD_SELL_STOP = 0, 1, 2, 3, 4, 5
_PENDING_CMDS = (_CMD_BUY_LIMIT, _CMD_SELL_LIMIT, _CMD_BUY_STOP, _CMD_SELL_STOP)
_TYPE_OPEN, _TYPE_PENDING, _TYPE_CLOSE, _TYPE_MODIFY, _TYPE_DELETE = 0, 1, 2, 3, 4
_REQ_STATUS = {0: OrderStatus.REJECTED, 1: OrderStatus.NEW, 3: OrderStatus.ACCEPTED, 4: OrderStatus.REJECTED}


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class XtbSession:
    """Shared login + main socket + stream socket. One per process, used by both adapters."""

    _instances: dict[str, "XtbSession"] = {}

    def __init__(self, user_id: str, password: str, demo: bool = True):
        import websockets  # noqa: F401  (import check)

        self.user_id, self.password = user_id, password
        self.demo = demo
        base = "wss://ws.xtb.com/" + ("demo" if demo else "real")
        self.main_url, self.stream_url = base, base + "Stream"
        self.ws = None
        self.stream_ws = None
        self.session_id: str | None = None
        self._lock = asyncio.Lock()
        self._last_req = 0.0
        self._ping_task: asyncio.Task | None = None
        self._refs = 0

    @classmethod
    def from_env(cls) -> "XtbSession":
        uid, pw = os.environ.get("XTB_USER_ID"), os.environ.get("XTB_PASSWORD")
        if not uid or not pw:
            raise RuntimeError("XTB_USER_ID / XTB_PASSWORD not set")
        demo = env_bool("XTB_DEMO", True)
        key = f"{uid}:{demo}"
        if key not in cls._instances:
            cls._instances[key] = cls(uid, pw, demo)
        return cls._instances[key]

    async def connect(self) -> None:
        import websockets

        self._refs += 1
        if self.ws is not None:
            return
        self.ws = await websockets.connect(self.main_url, max_size=2**24)
        res = await self.command("login", userId=self.user_id, password=self.password)
        self.session_id = res["streamSessionId"]
        self.stream_ws = await websockets.connect(self.stream_url, max_size=2**24)
        self._ping_task = asyncio.create_task(self._keepalive())
        log.info("XTB logged in (%s, demo=%s)", self.main_url, self.demo)

    async def close(self) -> None:
        self._refs -= 1
        if self._refs > 0 or self.ws is None:
            return
        if self._ping_task:
            self._ping_task.cancel()
        try:
            await self.command("logout")
        except Exception:  # noqa: BLE001
            pass
        await self.ws.close()
        if self.stream_ws:
            await self.stream_ws.close()
        self.ws = self.stream_ws = None

    async def command(self, cmd: str, **arguments: Any) -> Any:
        assert self.ws is not None
        async with self._lock:
            wait = 0.21 - (time.monotonic() - self._last_req)
            if wait > 0:
                await asyncio.sleep(wait)
            msg: dict[str, Any] = {"command": cmd}
            if arguments:
                msg["arguments"] = arguments
            await self.ws.send(json.dumps(msg))
            self._last_req = time.monotonic()
            raw = json.loads(await self.ws.recv())
        if not raw.get("status"):
            raise RuntimeError(f"xtb {cmd} failed: {raw.get('errorCode')} {raw.get('errorDescr')}")
        return raw.get("returnData", raw)

    async def stream_send(self, cmd: str, **extra: Any) -> None:
        assert self.stream_ws is not None and self.session_id
        await self.stream_ws.send(json.dumps({"command": cmd, "streamSessionId": self.session_id, **extra}))

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await self.command("ping")
                await self.stream_send("ping")
            except Exception as e:  # noqa: BLE001
                log.warning("xtb ping failed: %s", e)


class XtbData(MarketDataProvider):
    name = "xtb"

    def __init__(self, session: XtbSession | None = None, mapper: SymbolMapper | None = None):
        self.s = session or XtbSession.from_env()
        self.map = mapper or SymbolMapper(os.environ.get("XTB_SYMBOL_SUFFIX", ".US"))

    async def connect(self) -> None:
        await self.s.connect()

    async def close(self) -> None:
        await self.s.close()

    async def subscribe(self, symbols: Iterable[str]) -> None:
        for sym in symbols:
            await self.s.stream_send("getTickPrices", symbol=self.map.to_provider(sym), minArrivalTime=200, maxLevel=0)

    async def stream(self) -> AsyncIterator[Tick | Bar]:
        assert self.s.stream_ws is not None
        async for raw in self.s.stream_ws:
            msg = json.loads(raw)
            if msg.get("command") != "tickPrices":
                continue
            d = msg["data"]
            if d.get("level", 0) != 0:
                continue
            yield Tick(
                self.map.to_canonical(d["symbol"]),
                float(d["bid"]),
                float(d["ask"]),
                _ms_to_dt(d["timestamp"]),
                self.name,
            )

    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime | None = None
    ) -> list[Bar]:
        res = await self.s.command(
            "getChartLastRequest",
            info={
                "period": _PERIOD[timeframe],
                "start": int(start.timestamp() * 1000),
                "symbol": self.map.to_provider(symbol),
            },
        )
        scale = 10 ** int(res["digits"])
        out = []
        for r in res["rateInfos"]:
            o = r["open"] / scale
            out.append(
                Bar(
                    symbol,
                    timeframe,
                    o,
                    o + r["high"] / scale,
                    o + r["low"] / scale,
                    o + r["close"] / scale,
                    float(r.get("vol", 0)),
                    _ms_to_dt(r["ctm"]),
                    self.name,
                )
            )
        if end:
            out = [b for b in out if b.start <= end]
        return out


class XtbBroker(Broker):
    name = "xtb"

    def __init__(self, session: XtbSession | None = None, mapper: SymbolMapper | None = None, poll_sec: float = 2.0):
        self.s = session or XtbSession.from_env()
        self.paper = bool(getattr(self.s, "demo", False))
        self.map = mapper or SymbolMapper(os.environ.get("XTB_SYMBOL_SUFFIX", ".US"))
        self.poll_sec = poll_sec
        self._pending: dict[str, Order] = {}

    async def connect(self) -> None:
        await self.s.connect()

    async def close(self) -> None:
        await self.s.close()

    async def _quote(self, provider_symbol: str) -> tuple[float, float]:
        d = await self.s.command("getSymbol", symbol=provider_symbol)
        return float(d["bid"]), float(d["ask"])

    async def submit_order(self, req: OrderRequest) -> Order:
        psym = self.map.to_provider(req.symbol)
        bid, ask = await self._quote(psym)
        positions = await self._open_trades()
        existing = [p for p in positions if p["symbol"] == psym]

        # Reduce/close an existing opposite-side position first (required for real stocks).
        if existing and ((req.side == Side.SELL) == (existing[0]["cmd"] == _CMD_BUY)):
            pos = existing[0]
            vol = min(req.qty, float(pos["volume"]))
            info = {
                "cmd": pos["cmd"],
                "type": _TYPE_CLOSE,
                "order": pos["position"],
                "symbol": psym,
                "volume": vol,
                "price": bid if pos["cmd"] == _CMD_BUY else ask,
                "customComment": req.client_order_id[:30],
                "sl": 0,
                "tp": 0,
                "offset": 0,
                "expiration": 0,
            }
        else:
            if req.type == OrderType.LIMIT:
                cmd = _CMD_BUY_LIMIT if req.side == Side.BUY else _CMD_SELL_LIMIT
                price = req.limit_price
            else:
                cmd = _CMD_BUY if req.side == Side.BUY else _CMD_SELL
                price = ask if req.side == Side.BUY else bid
            info = {
                "cmd": cmd,
                "type": _TYPE_OPEN,
                "order": 0,
                "symbol": psym,
                "volume": req.qty,
                "price": price,
                "customComment": req.client_order_id[:30],
                "sl": 0,
                "tp": 0,
                "offset": 0,
                "expiration": 0,
            }

        res = await self.s.command("tradeTransaction", tradeTransInfo=info)
        order = Order(
            id=str(res["order"]),
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            status=OrderStatus.NEW,
            source=self.name,
            raw={
                "response": res,
                "cmd": info["cmd"],
                "type": info["type"],
                "volume": info["volume"],
                "price": info["price"],
            },
        )
        order = await self._refresh(order)
        if not order.status.is_terminal:
            self._pending[order.id] = order
        return order

    async def _refresh(self, order: Order) -> Order:
        st = await self.s.command("tradeTransactionStatus", order=int(order.id))
        status = _REQ_STATUS.get(int(st.get("requestStatus", -1)), OrderStatus.UNKNOWN)
        meta = order.raw if isinstance(order.raw, dict) else {}
        cmd = meta.get("cmd")
        is_market = cmd is None or cmd in (_CMD_BUY, _CMD_SELL)
        if status == OrderStatus.ACCEPTED and is_market:
            # xAPI "accepted" on a market OPEN/CLOSE means executed. The status payload carries no
            # fill price, so report the price we sent; the exact fill is in getTrades/open_price.
            # A pending (limit/stop) order stays ACCEPTED: polling tradeTransactionStatus never
            # observes its later fill (roadmap: switch to the getTrades stream).
            status = OrderStatus.FILLED
            order.filled_qty = float(meta.get("volume", order.qty))
            order.avg_fill_price = float(meta["price"]) if meta.get("price") is not None else None
        order.status = status
        if st.get("message"):
            log.info("xtb order %s: %s", order.id, st["message"])
        return order

    async def _delete_pending(self, rec: dict) -> None:
        """Delete one pending order; `rec` is its getTrades record."""
        info = {
            "cmd": rec["cmd"],
            "type": _TYPE_DELETE,
            "order": int(rec["order"]),
            "symbol": rec["symbol"],
            "volume": float(rec.get("volume") or 0),
            "price": float(rec.get("open_price") or 0),
            "sl": 0,
            "tp": 0,
            "offset": 0,
            "expiration": 0,
            "customComment": "",
        }
        res = await self.s.command("tradeTransaction", tradeTransInfo=info)
        st = await self.s.command("tradeTransactionStatus", order=int(res["order"]))
        if _REQ_STATUS.get(int(st.get("requestStatus", -1))) == OrderStatus.REJECTED:
            raise RuntimeError(f"xtb delete of order {rec['order']} rejected: {st.get('message')}")

    async def cancel_order(self, order_id: str) -> None:
        """Delete a pending (limit/stop) order. Market orders fill immediately and cannot be cancelled."""
        for t in await self.s.command("getTrades", openedOnly=False):
            if str(t.get("order")) == str(order_id) and t.get("cmd") in _PENDING_CMDS and not t.get("closed"):
                await self._delete_pending(t)
                self._pending.pop(order_id, None)
                return
        raise RuntimeError(f"xtb order {order_id} is not an open pending order")

    async def cancel_all(self) -> None:
        """Delete every pending order on the account. Open positions are not touched."""
        for t in await self.s.command("getTrades", openedOnly=False):
            if t.get("cmd") in _PENDING_CMDS and not t.get("closed"):
                try:
                    await self._delete_pending(t)
                except Exception as e:  # noqa: BLE001
                    log.warning("cancel %s failed: %s", t.get("order"), e)

    async def get_order(self, order_id: str) -> Order:
        o = self._pending.get(order_id) or Order(order_id, "", "", Side.BUY, 0, OrderStatus.UNKNOWN, source=self.name)
        return await self._refresh(o)

    async def _open_trades(self) -> list[dict]:
        return [t for t in await self.s.command("getTrades", openedOnly=True) if t.get("cmd") in (_CMD_BUY, _CMD_SELL)]

    async def get_positions(self) -> list[Position]:
        agg: dict[str, Position] = {}
        for t in await self._open_trades():
            sym = self.map.to_canonical(t["symbol"])
            qty = float(t["volume"]) * (1 if t["cmd"] == _CMD_BUY else -1)
            p = agg.get(sym)
            if p is None:
                agg[sym] = Position(
                    sym,
                    qty,
                    float(t["open_price"]),
                    unrealized_pnl=float(t.get("profit") or 0),
                    provider_ref=str(t["position"]),
                )
            else:
                tot = p.qty + qty
                p.avg_price = (p.avg_price * p.qty + float(t["open_price"]) * qty) / tot if tot else p.avg_price
                p.qty = tot
                p.unrealized_pnl = (p.unrealized_pnl or 0) + float(t.get("profit") or 0)
        return list(agg.values())

    async def get_account(self) -> Account:
        m = await self.s.command("getMarginLevel")
        return Account(float(m["equity"]), float(m["balance"]), float(m["margin_free"]), m.get("currency", "PLN"))

    async def order_updates(self) -> AsyncIterator[Order]:
        """xAPI has a `getTrades` stream, but polling tradeTransactionStatus is simpler and
        sufficient for a prototype."""
        while True:
            await asyncio.sleep(self.poll_sec)
            for oid, o in list(self._pending.items()):
                try:
                    o = await self._refresh(o)
                except Exception as e:  # noqa: BLE001
                    log.warning("refresh %s failed: %s", oid, e)
                    continue
                if o.status.is_terminal:
                    del self._pending[oid]
                    yield o
