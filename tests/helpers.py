"""Shared test helpers. Plain functions, not fixtures: `from helpers import ticks, wave`."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from tradesignals.models import Bar, Tick, Timeframe

#: Fixed epoch for replay tests whose outcome must not depend on the calendar.
T0 = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)


def now_floor(step_sec: int = 60) -> datetime:
    """Wall clock floored to a bucket boundary, for tests that touch wall-clock code paths
    (the engine's warm-up window is computed from utcnow())."""
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % step_sec, tz=timezone.utc)


def ticks(
    symbol: str, prices: list[float], step_sec: int = 60, start: datetime = T0, source: str = "test"
) -> list[Tick]:
    """One quote tick per price, `step_sec` apart, spread 0.02 around the price (mid == price)."""
    return [
        Tick(symbol, p - 0.01, p + 0.01, start + timedelta(seconds=i * step_sec), source) for i, p in enumerate(prices)
    ]


def bars(
    symbol: str, closes: list[float], tf: Timeframe = Timeframe.M1, start: datetime = T0, source: str = "test"
) -> list[Bar]:
    """Flat bars (open == high == low == close), one per timeframe step, volume 0."""
    step = timedelta(seconds=tf.seconds)
    return [Bar(symbol, tf, c, c, c, c, 0.0, start + i * step, source) for i, c in enumerate(closes)]


def wave(n: int) -> list[float]:
    """100 +/- 10 sine wave with a period of ~50 samples: several SMA crossovers in 120 samples."""
    return [100 + 10 * math.sin(i / 8) for i in range(n)]
