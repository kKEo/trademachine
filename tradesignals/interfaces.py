"""Abstraction layer. A provider implements MarketDataProvider, Broker, or both.

The engine only ever talks to these two classes and the SymbolMapper.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from datetime import datetime

from .models import Account, Bar, Order, OrderRequest, Position, Tick, Timeframe


class SymbolMapper:
    """Canonical symbol ("AAPL") <-> provider symbol ("AAPL.US_9")."""

    def __init__(self, suffix: str = "", overrides: dict[str, str] | None = None):
        self.suffix = suffix
        self.overrides = overrides or {}
        self._reverse = {v: k for k, v in self.overrides.items()}

    def to_provider(self, symbol: str) -> str:
        return self.overrides.get(symbol, f"{symbol}{self.suffix}")

    def to_canonical(self, provider_symbol: str) -> str:
        if provider_symbol in self._reverse:
            return self._reverse[provider_symbol]
        if self.suffix and provider_symbol.endswith(self.suffix):
            return provider_symbol[: -len(self.suffix)]
        return provider_symbol


class MarketDataProvider(ABC):
    name: str = "base"
    #: True when the provider pushes its own bars (Alpaca does). The engine then never builds
    #: bars from ticks for this provider, so a strategy sees each bucket exactly once.
    pushes_bars: bool = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def subscribe(self, symbols: Iterable[str]) -> None:
        """Subscribe to live ticks (and bars where the provider pushes them)."""

    @abstractmethod
    def stream(self) -> AsyncIterator[Tick | Bar]:
        """Yield normalized Tick/Bar events until closed."""

    @abstractmethod
    async def get_bars(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime | None = None
    ) -> list[Bar]:
        """Historical bars, oldest first. Used for strategy warm-up."""


class Broker(ABC):
    name: str = "base"
    #: True only when the adapter knows it targets a paper/demo account. Defaults to False so a
    #: forgotten attribute fails closed: the CLI refuses --execute against a broker whose
    #: ``paper`` is False unless --allow-live is given.
    paper: bool = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def submit_order(self, req: OrderRequest) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    async def cancel_all(self) -> None: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> Order: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_account(self) -> Account: ...

    @abstractmethod
    def order_updates(self) -> AsyncIterator[Order]:
        """Push stream of order state changes (poll-based adapters emulate it)."""
