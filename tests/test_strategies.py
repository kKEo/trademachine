from helpers import bars

from tradesignals.models import SignalKind, Timeframe
from tradesignals.strategies import REGISTRY, RsiReversion, SmaCross


def test_registry_names():
    assert set(REGISTRY) == {"sma_cross", "rsi_reversion"}
    assert REGISTRY["sma_cross"] is SmaCross and REGISTRY["rsi_reversion"] is RsiReversion


def test_sma_cross_emits_long_on_golden_cross_and_flat_on_death_cross():
    st = SmaCross(["AAPL"], fast=3, slow=5)
    closes = [10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 5, 4]
    series = bars("AAPL", closes)
    sigs = [(i, s) for i, s in enumerate(st.on_bar(b) for b in series) if s]
    assert [s.kind for _, s in sigs] == [SignalKind.LONG, SignalKind.FLAT]
    assert [i for i, _ in sigs] == [8, 13]
    assert sigs[0][1].ts == series[8].start and sigs[0][1].strategy == "sma_cross"
    assert sigs[0][1].reason.startswith("fast=")


def test_sma_cross_defaults_warmup_and_unknown_symbol():
    st = SmaCross(["AAPL"])
    assert (st.timeframe, st.warmup_bars) == (Timeframe.M1, 31)
    assert st.on_bar(bars("MSFT", [1.0])[0]) is None


def test_rsi_reversion_enters_when_oversold_and_exits_above_exit_level():
    st = RsiReversion(["AAPL"], n=3, oversold=30, exit=55)
    assert (st.timeframe, st.warmup_bars) == (Timeframe.M5, 9)
    closes = [10, 9, 8, 7, 6, 7, 8, 9, 10, 11]
    sigs = [(i, s) for i, s in enumerate(st.on_bar(b) for b in bars("AAPL", closes, tf=Timeframe.M5)) if s]
    assert [(i, s.kind) for i, s in sigs] == [(3, SignalKind.LONG), (6, SignalKind.FLAT)]
    assert sigs[0][1].strength == 1.0  # RSI 0 -> maximum strength


def test_rsi_reversion_strength_is_between_half_and_one():
    st = RsiReversion(["AAPL"], n=2, oversold=30, exit=55)
    strengths = [
        s.strength
        for s in (st.on_bar(b) for b in bars("AAPL", [10, 9.9, 9.8, 9.0, 10, 11, 12], tf=Timeframe.M5))
        if s and s.kind == SignalKind.LONG
    ]
    assert strengths and all(0.5 < x <= 1.0 for x in strengths)
