"""Strategy contract. A strategy is pure: it receives bars/ticks and may emit a Signal.
It never touches a broker and never knows which provider the data came from."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models import Bar, Signal, Tick, Timeframe


class Strategy(ABC):
    name: str = "strategy"
    timeframe: Timeframe = Timeframe.M1
    warmup_bars: int = 0  # how many historical bars to replay before going live

    def __init__(self, symbols: Iterable[str]):
        self.symbols = list(symbols)

    @abstractmethod
    def on_bar(self, bar: Bar) -> Signal | None: ...

    def on_tick(self, tick: Tick) -> Signal | None:  # optional, for tick-level logic
        return None
