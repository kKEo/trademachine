"""Small, dependency-free incremental indicators."""
from __future__ import annotations

from collections import deque


class SMA:
    def __init__(self, n: int):
        self.n = n
        self._buf: deque[float] = deque(maxlen=n)
        self._sum = 0.0

    def update(self, x: float) -> float | None:
        if len(self._buf) == self.n:
            self._sum -= self._buf[0]
        self._buf.append(x)
        self._sum += x
        return self.value

    @property
    def value(self) -> float | None:
        return self._sum / self.n if len(self._buf) == self.n else None

    @property
    def ready(self) -> bool:
        return len(self._buf) == self.n


class EMA:
    def __init__(self, n: int):
        self.k = 2 / (n + 1)
        self.value: float | None = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else x * self.k + self.value * (1 - self.k)
        return self.value


class RSI:
    """Wilder's RSI."""

    def __init__(self, n: int = 14):
        self.n = n
        self._prev: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._seed_g: list[float] = []
        self._seed_l: list[float] = []
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        ch = close - self._prev
        self._prev = close
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if self._avg_gain is None:
            self._seed_g.append(g)
            self._seed_l.append(l)
            if len(self._seed_g) < self.n:
                return None
            self._avg_gain = sum(self._seed_g) / self.n
            self._avg_loss = sum(self._seed_l) / self.n
        else:
            self._avg_gain = (self._avg_gain * (self.n - 1) + g) / self.n
            self._avg_loss = (self._avg_loss * (self.n - 1) + l) / self.n
        if self._avg_loss == 0:
            self.value = 100.0
        else:
            rs = self._avg_gain / self._avg_loss
            self.value = 100 - 100 / (1 + rs)
        return self.value
