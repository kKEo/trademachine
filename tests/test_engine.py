import math
from datetime import datetime, timedelta, timezone

import pytest

from tradesignals.bars import BarAggregator
from tradesignals.engine import SignalEngine
from tradesignals.interfaces import SymbolMapper
from tradesignals.models import Bar, SignalKind, Tick, Timeframe
from tradesignals.providers.fake import PaperBroker, ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.strategies import SmaCross

T0 = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)


def ticks(symbol: str, prices: list[float], step_sec: int = 60) -> list[Tick]:
    return [Tick(symbol, p - 0.01, p + 0.01, T0 + timedelta(seconds=i * step_sec), "test")
            for i, p in enumerate(prices)]


def wave(n: int) -> list[float]:
    # down then up then down: produces a golden cross then a death cross
    return [100 + 10 * math.sin(i / 8) for i in range(n)]


def test_symbol_mapper_roundtrip():
    m = SymbolMapper(".US_9", overrides={"BRK.B": "BRKB.US_9"})
    assert m.to_provider("AAPL") == "AAPL.US_9"
    assert m.to_canonical("AAPL.US_9") == "AAPL"
    assert m.to_provider("BRK.B") == "BRKB.US_9"
    assert m.to_canonical("BRKB.US_9") == "BRK.B"


def test_bar_aggregator_rolls_over():
    agg = BarAggregator("AAPL", Timeframe.M1)
    out = [agg.on_tick(t) for t in ticks("AAPL", [10, 11, 9, 12], step_sec=20)]
    bars = [b for b in out if b]
    assert len(bars) == 1
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (10, 11, 9, 9)


async def test_signal_only_mode_emits_without_orders():
    data = ReplayProvider(ticks("AAPL", wave(120)))
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    eng = SignalEngine(data, [strat], warmup=False)
    await eng.run()
    kinds = [s.kind for s in eng.signals]
    assert SignalKind.LONG in kinds and SignalKind.FLAT in kinds
    assert eng.orders == []


async def test_execute_mode_trades_paper_broker():
    tk = ticks("AAPL", wave(120))
    data = ReplayProvider(tk)
    broker = PaperBroker()
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    risk = RiskManager(RiskLimits(max_order_notional=10_000, max_quote_age_sec=10**9))
    eng = SignalEngine(data, [strat], broker=broker, risk=risk, execute=True, default_qty=3, warmup=False)

    orig = eng._on_tick

    async def on_tick(t):
        broker.mark(t.symbol, t.price)
        await orig(t)
    eng._on_tick = on_tick

    await eng.run()
    # a leading FLAT with no position is a no-op; afterwards LONG/FLAT alternate -> buy/sell
    assert len(eng.orders) >= 2
    sides = [o.side.value for o in eng.orders]
    assert sides[0] == "buy" and all(a != b for a, b in zip(sides, sides[1:]))
    assert all(o.qty == 3 for o in eng.orders)


async def test_risk_rejects_oversized_order():
    data = ReplayProvider(ticks("AAPL", wave(120)))
    broker = PaperBroker()
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    risk = RiskManager(RiskLimits(max_order_notional=50, max_quote_age_sec=10**9))  # ~$100/share
    eng = SignalEngine(data, [strat], broker=broker, risk=risk, execute=True, default_qty=1, warmup=False)
    orig = eng._on_tick

    async def on_tick(t):
        broker.mark(t.symbol, t.price)
        await orig(t)
    eng._on_tick = on_tick
    await eng.run()
    assert eng.signals and eng.orders == []


async def test_warmup_uses_history():
    hist = [Bar("AAPL", Timeframe.M1, p, p, p, p, 0, T0 - timedelta(minutes=200 - i), "h")
            for i, p in enumerate(wave(200))]
    data = ReplayProvider([], history={"AAPL": hist})
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    eng = SignalEngine(data, [strat], warmup=True)
    await eng.run()
    assert strat._slow["AAPL"].ready
