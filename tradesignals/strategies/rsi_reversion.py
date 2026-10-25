from __future__ import annotations

from ..indicators import RSI
from ..models import Bar, Signal, SignalKind, Timeframe
from .base import Strategy


class RsiReversion(Strategy):
    """Mean reversion: LONG when RSI < oversold, FLAT when RSI > exit level."""

    name = "rsi_reversion"

    def __init__(
        self, symbols, n: int = 14, oversold: float = 30, exit: float = 55, timeframe: Timeframe = Timeframe.M5
    ):
        super().__init__(symbols)
        self.timeframe = timeframe
        self.warmup_bars = n * 3
        self.oversold, self.exit = oversold, exit
        self._rsi = {s: RSI(n) for s in self.symbols}
        self._in = {s: False for s in self.symbols}

    def on_bar(self, bar: Bar) -> Signal | None:
        if bar.symbol not in self._rsi:
            return None
        v = self._rsi[bar.symbol].update(bar.close)
        if v is None:
            return None
        if not self._in[bar.symbol] and v < self.oversold:
            self._in[bar.symbol] = True
            return Signal(
                bar.symbol,
                SignalKind.LONG,
                self.name,
                bar.start,
                strength=min(1.0, (self.oversold - v) / self.oversold + 0.5),
                reason=f"rsi={v:.1f}",
            )
        if self._in[bar.symbol] and v > self.exit:
            self._in[bar.symbol] = False
            return Signal(bar.symbol, SignalKind.FLAT, self.name, bar.start, reason=f"rsi={v:.1f}")
        return None
