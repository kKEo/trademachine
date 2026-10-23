"""ReplayProvider + PaperBroker: run the whole pipeline without any network.

Also the reference implementation of the interfaces: if a real adapter behaves
differently from these, the adapter is probably wrong.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from uuid import uuid4

from ..interfaces import Broker, MarketDataProvider
from ..models import (
    Account, Bar, Order, OrderRequest, OrderStatus, Position, Side, Tick, Timeframe, utcnow,
)


class ReplayProvider(MarketDataProvider):
    name = "replay"

    def __init__(self, events: Iterable[Tick | Bar], history: dict[str, list[Bar]] | None = None,
                 delay: float = 0.0):
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

    async def get_bars(self, symbol: str, timeframe: Timeframe, start: datetime,
                       end: datetime | None = None) -> list[Bar]:
        return [b for b in self._history.get(symbol, []) if b.timeframe == timeframe and b.start >= start]


class PaperBroker(Broker):
    """Fills market orders instantly at the price fed via `mark(symbol, price)`."""

    name = "paper"

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
        price = req.limit_price or self.marks.get(req.symbol)
        if price is None:
            raise RuntimeError(f"no mark for {req.symbol}")
        order = Order(id=uuid4().hex[:8], client_order_id=req.client_order_id, symbol=req.symbol,
                      side=req.side, qty=req.qty, status=OrderStatus.FILLED, filled_qty=req.qty,
                      avg_fill_price=price, source=self.name)
        signed = req.qty if req.side == Side.BUY else -req.qty
        pos = self.positions.get(req.symbol)
        new_qty = (pos.qty if pos else 0.0) + signed
        if pos and new_qty != 0 and (pos.qty > 0) == (signed > 0):
            avg = (pos.avg_price * pos.qty + price * signed) / new_qty
        else:
            avg = price
        if abs(new_qty) < 1e-9:
            self.positions.pop(req.symbol, None)
        else:
            self.positions[req.symbol] = Position(req.symbol, new_qty, avg)
        self.cash -= signed * price
        self.orders[order.id] = order
        await self._updates.put(order)
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
