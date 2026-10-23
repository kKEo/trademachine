"""Tick -> Bar aggregation, so strategies can run on any provider even if it only
pushes ticks (XTB) or a different bar size than the strategy wants."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import Bar, Tick, Timeframe


def bucket_start(ts: datetime, tf: Timeframe) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % tf.seconds, tz=timezone.utc)


class BarAggregator:
    """One aggregator per (symbol, timeframe). Emits a Bar when the bucket rolls over."""

    def __init__(self, symbol: str, timeframe: Timeframe):
        self.symbol = symbol
        self.tf = timeframe
        self._cur: Bar | None = None

    def on_tick(self, tick: Tick) -> Bar | None:
        return self._update(tick.price, 0.0, tick.ts, tick.source)

    def on_bar(self, bar: Bar) -> Bar | None:
        """Roll finer bars (e.g. provider 1m) into coarser ones (e.g. 5m)."""
        if bar.timeframe == self.tf:
            return bar
        out = self._update(bar.open, 0.0, bar.start, bar.source)
        self._update(bar.high, 0.0, bar.start, bar.source)
        self._update(bar.low, 0.0, bar.start, bar.source)
        self._update(bar.close, bar.volume, bar.start, bar.source)
        return out

    def _update(self, price: float, volume: float, ts: datetime, source: str) -> Bar | None:
        start = bucket_start(ts, self.tf)
        completed: Bar | None = None
        cur = self._cur
        if cur is None or cur.start != start:
            if cur is not None and start > cur.start:
                completed = cur
            self._cur = Bar(self.symbol, self.tf, price, price, price, price, volume, start, source)
            return completed
        self._cur = Bar(
            self.symbol, self.tf, cur.open, max(cur.high, price), min(cur.low, price),
            price, cur.volume + volume, cur.start, source,
        )
        return None

    @property
    def current(self) -> Bar | None:
        return self._cur
