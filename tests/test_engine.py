from datetime import timedelta

from helpers import bars, now_floor, ticks, wave

from tradesignals.bars import BarAggregator
from tradesignals.engine import SignalEngine
from tradesignals.interfaces import SymbolMapper
from tradesignals.models import SignalKind, Timeframe
from tradesignals.providers.fake import PaperBroker, ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.strategies import SmaCross


def test_symbol_mapper_roundtrip():
    m = SymbolMapper(".US_9", overrides={"BRK.B": "BRKB.US_9"})
    assert m.to_provider("AAPL") == "AAPL.US_9"
    assert m.to_canonical("AAPL.US_9") == "AAPL"
    assert m.to_provider("BRK.B") == "BRKB.US_9"
    assert m.to_canonical("BRKB.US_9") == "BRK.B"


def test_bar_aggregator_rolls_over():
    agg = BarAggregator("AAPL", Timeframe.M1)
    out = [agg.on_tick(t) for t in ticks("AAPL", [10, 11, 9, 12], step_sec=20)]
    bars_out = [b for b in out if b]
    assert len(bars_out) == 1
    assert (bars_out[0].open, bars_out[0].high, bars_out[0].low, bars_out[0].close) == (10, 11, 9, 9)


async def test_signal_only_mode_emits_without_orders():
    data = ReplayProvider(ticks("AAPL", wave(120)))
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    eng = SignalEngine(data, [strat], warmup=False)
    await eng.run()
    kinds = [s.kind for s in eng.signals]
    assert SignalKind.LONG in kinds and SignalKind.FLAT in kinds
    assert eng.orders == []


async def test_execute_mode_trades_paper_broker():
    data = ReplayProvider(ticks("AAPL", wave(120)))
    broker = PaperBroker()
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    risk = RiskManager(RiskLimits(max_order_notional=10_000, max_quote_age_sec=10**9))
    eng = SignalEngine(
        data,
        [strat],
        broker=broker,
        risk=risk,
        execute=True,
        default_qty=3,
        warmup=False,
        on_tick=lambda t: broker.mark(t.symbol, t.price),
    )
    await eng.run()
    # the first signal of the wave is FLAT with no position (a no-op); afterwards LONG/FLAT alternate
    assert len(eng.orders) >= 2
    sides = [o.side.value for o in eng.orders]
    assert sides[0] == "buy" and all(a != b for a, b in zip(sides, sides[1:]))
    assert all(o.qty == 3 and o.status.value == "filled" for o in eng.orders)


async def test_risk_rejects_oversized_order():
    data = ReplayProvider(ticks("AAPL", wave(120)))
    broker = PaperBroker()
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    risk = RiskManager(RiskLimits(max_order_notional=50, max_quote_age_sec=10**9))  # ~$100/share
    eng = SignalEngine(
        data,
        [strat],
        broker=broker,
        risk=risk,
        execute=True,
        default_qty=1,
        warmup=False,
        on_tick=lambda t: broker.mark(t.symbol, t.price),
    )
    await eng.run()
    assert eng.signals and eng.orders == []


async def test_warmup_uses_history():
    # The warm-up window is anchored to the wall clock (16 bars * 60 s * 3 = 48 min back), so the
    # history must end "now": ending at a fixed date made this test fail once that date passed.
    end = now_floor()
    hist = bars("AAPL", wave(200), start=end - timedelta(minutes=200))
    data = ReplayProvider([], history={"AAPL": hist})
    strat = SmaCross(["AAPL"], fast=5, slow=15)
    eng = SignalEngine(data, [strat], warmup=True)
    await eng.run()
    assert strat._slow["AAPL"].ready
