"""ReplayProvider + PaperBroker: run the whole pipeline without any network.

Also the reference implementation of the interfaces: if a real adapter behaves
differently from these, the adapter is probably wrong.

PaperBroker semantics:
- Market orders fill instantly at the last mark fed via ``mark(symbol, price)`` (the CLI and
  the engine's ``on_tick`` callback feed tick prices). No spread, slippage or fees.
- Limit orders fill at the mark only if marketable (buy: mark <= limit, sell: mark >= limit);
  otherwise they are returned as REJECTED without touching positions.
- Positions are netted per symbol. Adding averages the cost basis, reducing keeps it,
  flipping through zero restarts it at the fill price. A SELL with no position opens a short.
- ``cancel_order`` / ``cancel_all`` are no-ops because nothing ever rests on a book.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from uuid import uuid4

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
)


class ReplayProvider(MarketDataProvider):
    name = "replay"

    def __init__(
        self,
        events: Iterable[Tick | Bar],
        history: dict[str, list[Bar]] | None = None,
        delay: float = 0.0,
        pushes_bars: bool = False,
    ):
        self.pushes_bars = pushes_bars
        self._events = list(events)
        self._history = history or {}
        self._delay = delay
        self.subscribed: list[str] = []

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def subscribe(self, symbols: Iterable[str]) -> None:
        self.subscribed = list(symbols)

    async def stream(self) -> AsyncIterator[Tick | Bar]:
        for ev in self._events:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield ev

    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime | None = None
    ) -> list[Bar]:
        return [
            b
            for b in self._history.get(symbol, [])
            if b.timeframe == timeframe and b.start >= start and (end is None or b.start <= end)
        ]


class PaperBroker(Broker):
    """In-memory broker that fills at the price fed via `mark(symbol, price)`."""

    name = "paper"
    paper = True

    def __init__(self, cash: float = 100_000.0):
        self.cash = cash
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.marks: dict[str, float] = {}
        self._updates: asyncio.Queue[Order] = asyncio.Queue()

    def mark(self, symbol: str, price: float) -> None:
        self.marks[symbol] = price

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def submit_order(self, req: OrderRequest) -> Order:
        mark = self.marks.get(req.symbol)
        if mark is None:
            raise RuntimeError(f"no mark for {req.symbol}")
        if req.type == OrderType.LIMIT:
            if req.limit_price is None:
                raise ValueError("limit order without limit_price")
            marketable = mark <= req.limit_price if req.side == Side.BUY else mark >= req.limit_price
            if not marketable:
                order = self._order(req, OrderStatus.REJECTED, 0.0, None)
                await self._updates.put(order)
                return order
        price = mark
        order = self._order(req, OrderStatus.FILLED, req.qty, price)

        signed = req.qty if req.side == Side.BUY else -req.qty
        pos = self.positions.get(req.symbol)
        old_qty = pos.qty if pos else 0.0
        new_qty = old_qty + signed
        if pos is None or old_qty == 0:
            avg = price
        elif (old_qty > 0) == (signed > 0):
            avg = (pos.avg_price * old_qty + price * signed) / new_qty  # adding: weighted average
        elif abs(signed) <= abs(old_qty):
            avg = pos.avg_price  # reducing or closing: cost basis of the remainder is unchanged
        else:
            avg = price  # flipped through zero: the remainder was opened at this fill
        if abs(new_qty) < 1e-9:
            self.positions.pop(req.symbol, None)
        else:
            self.positions[req.symbol] = Position(req.symbol, new_qty, avg)
        self.cash -= signed * price
        await self._updates.put(order)
        return order

    def _order(self, req: OrderRequest, status: OrderStatus, filled_qty: float, avg_fill_price: float | None) -> Order:
        order = Order(
            id=uuid4().hex[:8],
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            source=self.name,
        )
        self.orders[order.id] = order
        return order

    async def cancel_order(self, order_id: str) -> None: ...

    async def cancel_all(self) -> None: ...

    async def get_order(self, order_id: str) -> Order:
        return self.orders[order_id]

    async def get_positions(self) -> list[Position]:
        out = []
        for p in self.positions.values():
            mark = self.marks.get(p.symbol, p.avg_price)
            out.append(Position(p.symbol, p.qty, p.avg_price, mark * p.qty, (mark - p.avg_price) * p.qty))
        return out

    async def get_account(self) -> Account:
        mv = sum(self.marks.get(p.symbol, p.avg_price) * p.qty for p in self.positions.values())
        return Account(equity=self.cash + mv, cash=self.cash, buying_power=self.cash)

    async def order_updates(self) -> AsyncIterator[Order]:
        while True:
            yield await self._updates.get()
