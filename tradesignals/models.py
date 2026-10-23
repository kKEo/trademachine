"""Provider-agnostic domain models.

Everything that crosses the provider boundary is one of these. Adapters translate
provider payloads into them and never leak provider types upward.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"


class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIAL = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}[self.value]


@dataclass(frozen=True, slots=True)
class Tick:
    symbol: str  # canonical symbol, e.g. "AAPL"
    bid: float
    ask: float
    ts: datetime
    source: str
    last: float | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def price(self) -> float:
        return self.last if self.last is not None else self.mid


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float
    start: datetime
    source: str


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: Side
    qty: float
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    tif: TimeInForce = TimeInForce.DAY
    client_order_id: str = field(default_factory=lambda: uuid4().hex)
    note: str = ""


@dataclass(slots=True)
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    ts: datetime = field(default_factory=utcnow)
    source: str = ""
    raw: Any = None


@dataclass(slots=True)
class Position:
    symbol: str
    qty: float  # signed: negative = short
    avg_price: float
    market_value: float | None = None
    unrealized_pnl: float | None = None
    provider_ref: str | None = None  # e.g. XTB position id, needed to close


@dataclass(slots=True)
class Account:
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"


class SignalKind(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    kind: SignalKind
    strategy: str
    ts: datetime
    strength: float = 1.0  # 0..1, lets the sizer scale
    reason: str = ""
