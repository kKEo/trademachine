# Adding a strategy

A strategy is one small, pure class. This chapter gives the contract, the two things the CLI assumes beyond the base class, a skeleton, and the test to write with it. Budget: an hour including the test.

## The contract

`Strategy` (`tradesignals/strategies/base.py`):

| Member | Meaning |
|---|---|
| `name: str` | registry key and the name printed in `SIGNAL` lines; must be unique |
| `timeframe: Timeframe` | bar size the engine aggregates to for this strategy (default `M1`) |
| `warmup_bars: int` | how many historical bars to replay before going live (default 0, meaning no warm-up) |
| `__init__(self, symbols)` | stores `self.symbols` as a list |
| `on_bar(self, bar) -> Signal \| None` | abstract; called once per complete bar of `timeframe` for one of `symbols` |
| `on_tick(self, tick) -> Signal \| None` | optional; called for every tick of one of `symbols`, before aggregation; returns `None` by default |

Rules the engine relies on:

1. **Pure.** No I/O, no broker access, no provider knowledge, no clock reads. Everything the strategy knows comes through `on_bar` and `on_tick`. This is what makes warm-up and replay tests possible.
2. **Per-symbol state.** `on_bar` receives bars for every symbol in `self.symbols` interleaved; keep indicator state in dicts keyed by symbol, and return `None` for a symbol you do not know.
3. **Signals mean target positions, not trades.** `LONG` means "hold `qty × strength`", `FLAT` means "hold nothing", `SHORT` means a negative target. The engine computes the delta against the broker position; emitting `LONG` twice in a row is harmless (second one is a `no-op`).
4. **`strength`** is documented as 0..1 and multiplies `--qty`; it is not validated (`tradesignals/models.py:149`). Keep it in range and remember that fractional results produce fractional share quantities.
5. **`SHORT`** opens a short on brokers that allow it. On XTB with the `.US` suffix that is a leveraged CFD position; with `.US_9` XTB rejects it. Neither built-in strategy emits it.
6. **Warm-up signals are discarded**, but the state they build is kept. If your strategy keeps an "in position" flag, it will reflect history, not the broker; see how `rsi_reversion` can start believing it is in a trade.
7. **No parameter validation exists upstream.** `SMA(0)` divides by zero; `SmaCross(fast=30, slow=10)` runs happily with inverted meaning. Validate in `__init__`.

## What the CLI assumes

`tradesignals/cli.py` builds the strategy as `REGISTRY[name](symbols, timeframe=Timeframe(...))` when `--timeframe` is given. Your constructor must therefore accept `symbols` positionally and `timeframe` as a keyword with a default, even though the base class does not declare it. Other parameters cannot be set from the command line; give them defaults.

## Skeleton

```python
# tradesignals/strategies/breakout.py
from __future__ import annotations

from collections import deque

from ..models import Bar, Signal, SignalKind, Timeframe
from .base import Strategy


class Breakout(Strategy):
    """LONG when the close exceeds the highest high of the last n bars, FLAT when it drops below the lowest low."""

    name = "breakout"

    def __init__(self, symbols, n: int = 20, timeframe: Timeframe = Timeframe.M5):
        super().__init__(symbols)
        if n < 2:
            raise ValueError("n must be at least 2")
        self.timeframe = timeframe
        self.warmup_bars = n + 1
        self._highs = {s: deque(maxlen=n) for s in self.symbols}
        self._lows = {s: deque(maxlen=n) for s in self.symbols}
        self._in = {s: False for s in self.symbols}

    def on_bar(self, bar: Bar) -> Signal | None:
        if bar.symbol not in self._highs:
            return None
        highs, lows = self._highs[bar.symbol], self._lows[bar.symbol]
        ready = len(highs) == highs.maxlen
        hi, lo = (max(highs), min(lows)) if ready else (None, None)
        highs.append(bar.high)
        lows.append(bar.low)
        if not ready:
            return None
        if not self._in[bar.symbol] and bar.close > hi:
            self._in[bar.symbol] = True
            return Signal(
                bar.symbol, SignalKind.LONG, self.name, bar.start, reason=f"close {bar.close:.2f} > high {hi:.2f}"
            )
        if self._in[bar.symbol] and bar.close < lo:
            self._in[bar.symbol] = False
            return Signal(
                bar.symbol, SignalKind.FLAT, self.name, bar.start, reason=f"close {bar.close:.2f} < low {lo:.2f}"
            )
        return None
```

Register it in `tradesignals/strategies/__init__.py`:

```python
from .breakout import Breakout

REGISTRY[Breakout.name] = Breakout  # or add it to the dict literal
```

The name then appears in `--strategy` choices automatically, and `scripts/docs-check.py` will ask you to mention it in [Commands and flags](../install/commands.md).

## The test

Feed a hand-made bar series and assert the exact signal positions, as `tests/test_strategies.py` does for the built-ins:

```python
from helpers import bars
from tradesignals.models import SignalKind, Timeframe
from tradesignals.strategies.breakout import Breakout


def test_breakout_enters_on_new_high_and_exits_on_new_low():
    st = Breakout(["AAPL"], n=3)
    closes = [10, 10, 10, 11, 11, 11, 9]
    sigs = [(i, s) for i, s in enumerate(st.on_bar(b) for b in bars("AAPL", closes, tf=Timeframe.M5)) if s]
    assert [(i, s.kind) for i, s in sigs] == [(3, SignalKind.LONG), (6, SignalKind.FLAT)]
```

Then run the whole pipeline once on a replay (`tests/test_engine.py::test_execute_mode_trades_paper_broker` is the template) to confirm the signals turn into the orders you expect. See the [Tester guide](../../tester/writing/adding-tests.md) for conventions.
