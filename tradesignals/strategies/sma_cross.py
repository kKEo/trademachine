from __future__ import annotations

from ..indicators import SMA
from ..models import Bar, Signal, SignalKind, Timeframe
from .base import Strategy


class SmaCross(Strategy):
    """Classic fast/slow SMA crossover. LONG on golden cross, FLAT on death cross."""

    name = "sma_cross"

    def __init__(self, symbols, fast: int = 10, slow: int = 30, timeframe: Timeframe = Timeframe.M1):
        super().__init__(symbols)
        self.timeframe = timeframe
        self.warmup_bars = slow + 1
        self._fast = {s: SMA(fast) for s in self.symbols}
        self._slow = {s: SMA(slow) for s in self.symbols}
        self._above: dict[str, bool | None] = {s: None for s in self.symbols}

    def on_bar(self, bar: Bar) -> Signal | None:
        if bar.symbol not in self._fast:
            return None
        f = self._fast[bar.symbol].update(bar.close)
        s = self._slow[bar.symbol].update(bar.close)
        if f is None or s is None:
            return None
        above = f > s
        prev = self._above[bar.symbol]
        self._above[bar.symbol] = above
        if prev is None or prev == above:
            return None
        kind = SignalKind.LONG if above else SignalKind.FLAT
        return Signal(bar.symbol, kind, self.name, bar.start, reason=f"fast={f:.2f} slow={s:.2f}")
