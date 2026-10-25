"""Alpaca adapter (alpaca-py). Free IEX feed + paper trading work out of the box.

Env: ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER=true|false, ALPACA_FEED=iex|sip

The stream pushes quote ticks and 1-minute bars for every subscribed symbol. The engine
builds bars from ticks only until the first provider bar arrives, then provider bars win.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone

from ..config import env_bool
from ..interfaces import Broker, MarketDataProvider
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
    TimeInForce,
)

log = logging.getLogger("tradesignals.alpaca")

# Attribute names of alpaca.data.timeframe.TimeFrameUnit. Look them up with TimeFrameUnit[name]:
# the enum *values* differ (Minute == "Min"), so TimeFrameUnit("Minute") raises ValueError.
_TF = {
    Timeframe.M1: ("Minute", 1),
    Timeframe.M5: ("Minute", 5),
    Timeframe.M15: ("Minute", 15),
    Timeframe.H1: ("Hour", 1),
    Timeframe.D1: ("Day", 1),
}
_STATUS = {
    "new": OrderStatus.NEW,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.NEW,
    "partially_filled": OrderStatus.PARTIAL,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}


def _enum_value(x) -> str:
    """alpaca-py enums are (str, Enum): str(OrderSide.BUY) == 'OrderSide.BUY', .value == 'buy'."""
    return str(getattr(x, "value", x)).lower()


def _opt_float(x) -> float | None:
    return None if x is None else float(x)


def _creds() -> tuple[str, str, bool]:
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return key, sec, env_bool("ALPACA_PAPER", True)


def _to_order(o) -> Order:
    return Order(
        id=str(o.id),
        client_order_id=o.client_order_id,
        symbol=o.symbol,
        side=Side.BUY if _enum_value(o.side).endswith("buy") else Side.SELL,
        qty=float(o.qty or 0),
        status=_STATUS.get(_enum_value(o.status).split(".")[-1], OrderStatus.UNKNOWN),
        filled_qty=float(o.filled_qty or 0),
        avg_fill_price=float(o.filled_avg_price) if o.filled_avg_price else None,
        source="alpaca",
        raw=o,
    )


class AlpacaData(MarketDataProvider):
    name = "alpaca"
    pushes_bars = True  # quotes AND 1-minute bars are streamed; the engine must not build bars from ticks

    def __init__(self):
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.live import StockDataStream

        key, sec, _ = _creds()
        self._feed = DataFeed.SIP if os.environ.get("ALPACA_FEED", "iex").lower() == "sip" else DataFeed.IEX
        self._hist = StockHistoricalDataClient(key, sec)
        self._stream = StockDataStream(key, sec, feed=self._feed)
        self._q: asyncio.Queue[Tick | Bar] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def connect(self) -> None: ...

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        await self._stream.close()

    async def subscribe(self, symbols: Iterable[str]) -> None:
        syms = list(symbols)

        async def on_quote(q):
            await self._q.put(
                Tick(q.symbol, float(q.bid_price), float(q.ask_price), q.timestamp.astimezone(timezone.utc), self.name)
            )

        async def on_bar(b):
            await self._q.put(
                Bar(
                    b.symbol,
                    Timeframe.M1,
                    float(b.open),
                    float(b.high),
                    float(b.low),
                    float(b.close),
                    float(b.volume),
                    b.timestamp.astimezone(timezone.utc),
                    self.name,
                )
            )

        self._stream.subscribe_quotes(on_quote, *syms)
        self._stream.subscribe_bars(on_bar, *syms)
        self._task = asyncio.create_task(self._stream._run_forever())
        log.info("alpaca data: feed=%s symbols=%s", _enum_value(self._feed), syms)

    async def stream(self) -> AsyncIterator[Tick | Bar]:
        while True:
            yield await self._q.get()

    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime | None = None
    ) -> list[Bar]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        unit, n = _TF[timeframe]
        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame(n, TimeFrameUnit[unit]), start=start, end=end, feed=self._feed
        )
        res = await asyncio.to_thread(self._hist.get_stock_bars, req)
        return [
            Bar(
                symbol,
                timeframe,
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
                float(b.volume),
                b.timestamp.astimezone(timezone.utc),
                self.name,
            )
            for b in res.data.get(symbol, [])
        ]


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self):
        from alpaca.trading.client import TradingClient
        from alpaca.trading.stream import TradingStream

        key, sec, paper = _creds()
        self.paper = paper
        self._client = TradingClient(key, sec, paper=paper)
        self._updates = TradingStream(key, sec, paper=paper)
        self._q: asyncio.Queue[Order] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        async def on_update(u):
            await self._q.put(_to_order(u.order))

        self._updates.subscribe_trade_updates(on_update)
        self._task = asyncio.create_task(self._updates._run_forever())
        log.info("alpaca broker connected (paper=%s)", self.paper)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        await self._updates.close()

    async def submit_order(self, req: OrderRequest) -> Order:
        from alpaca.trading.enums import OrderSide
        from alpaca.trading.enums import TimeInForce as ATIF
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if req.side == Side.BUY else OrderSide.SELL
        tif = ATIF.GTC if req.tif == TimeInForce.GTC else ATIF.DAY
        if req.type == OrderType.LIMIT:
            areq = LimitOrderRequest(
                symbol=req.symbol,
                qty=req.qty,
                side=side,
                time_in_force=tif,
                limit_price=req.limit_price,
                client_order_id=req.client_order_id,
            )
        else:
            areq = MarketOrderRequest(
                symbol=req.symbol, qty=req.qty, side=side, time_in_force=tif, client_order_id=req.client_order_id
            )
        return _to_order(await asyncio.to_thread(self._client.submit_order, areq))

    async def cancel_order(self, order_id: str) -> None:
        await asyncio.to_thread(self._client.cancel_order_by_id, order_id)

    async def cancel_all(self) -> None:
        """Cancels EVERY open order on the account, not only the ones this engine placed."""
        await asyncio.to_thread(self._client.cancel_orders)

    async def get_order(self, order_id: str) -> Order:
        return _to_order(await asyncio.to_thread(self._client.get_order_by_id, order_id))

    async def get_positions(self) -> list[Position]:
        ps = await asyncio.to_thread(self._client.get_all_positions)
        return [
            Position(
                p.symbol,
                float(p.qty),
                float(p.avg_entry_price),
                _opt_float(p.market_value),
                _opt_float(p.unrealized_pl),
            )
            for p in ps
        ]

    async def get_account(self) -> Account:
        a = await asyncio.to_thread(self._client.get_account)
        return Account(float(a.equity), float(a.cash), float(a.buying_power), a.currency or "USD")

    async def order_updates(self) -> AsyncIterator[Order]:
        while True:
            yield await self._q.get()
