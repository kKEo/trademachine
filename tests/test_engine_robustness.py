"""Engine behaviour around failures and mixed tick/bar streams."""

import asyncio
import logging
from datetime import timedelta

import pytest
from helpers import T0, bars, now_floor, ticks, wave

from tradesignals.engine import SignalEngine
from tradesignals.models import Bar, Timeframe
from tradesignals.providers.fake import PaperBroker, ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.strategies import SmaCross, Strategy


class Recorder(Strategy):
    """Records every bar it is given; emits nothing."""

    name = "recorder"

    def __init__(self, symbols, timeframe=Timeframe.M1):
        super().__init__(symbols)
        self.timeframe = timeframe
        self.bars: list[Bar] = []

    def on_bar(self, bar):
        self.bars.append(bar)
        return None


def _alpaca_like_stream(minutes: int, prices_per_minute: int = 3) -> list:
    """Quote ticks every 20 s plus the provider's own 1-minute bar, which (as with Alpaca)
    arrives after the first ticks of the *next* minute."""
    events: list = []
    for m in range(minutes + 1):
        start = T0 + timedelta(minutes=m)
        events += ticks("AAPL", [100 + m + i * 0.25 for i in range(prices_per_minute)], step_sec=20, start=start)
        if m > 0:
            prev = T0 + timedelta(minutes=m - 1)
            events.append(
                Bar("AAPL", Timeframe.M1, 100 + m - 1, 101 + m - 1, 99 + m - 1, 100.5 + m - 1, 1000.0, prev, "prov")
            )
    return events


async def test_provider_that_declares_bars_never_builds_them_from_ticks():
    strat = Recorder(["AAPL"])
    eng = SignalEngine(ReplayProvider(_alpaca_like_stream(4), pushes_bars=True), [strat], warmup=False)
    await eng.run()
    starts = [b.start for b in strat.bars]
    assert starts == [T0 + timedelta(minutes=m) for m in range(4)], "exactly one bar per bucket"
    assert all(b.source == "prov" for b in strat.bars)


async def test_declared_bar_provider_rolls_up_into_coarser_strategy_timeframe():
    strat = Recorder(["AAPL"], timeframe=Timeframe.M5)
    eng = SignalEngine(ReplayProvider(_alpaca_like_stream(11), pushes_bars=True), [strat], warmup=False)
    await eng.run()
    assert [b.start for b in strat.bars] == [T0, T0 + timedelta(minutes=5)]
    assert strat.bars[0].volume == 5000.0  # five provider bars, no tick noise


async def test_undeclared_provider_switches_to_bars_once_it_pushes_them():
    """Safety net: a provider that forgot `pushes_bars = True` still stops double-feeding after
    its first bar; only the buckets before that bar may be tick-built."""
    events = []
    for m in range(4):
        start = T0 + timedelta(minutes=m)
        events += ticks("AAPL", [50 + m, 50.5 + m], step_sec=20, start=start)
        events.append(Bar("AAPL", Timeframe.M1, 50 + m, 51 + m, 50 + m, 50.5 + m, 10.0, start, "prov"))
    strat = Recorder(["AAPL"])
    eng = SignalEngine(ReplayProvider(events), [strat], warmup=False)
    await eng.run()
    starts = [b.start for b in strat.bars]
    assert len(starts) == len(set(starts)), "a bucket reached the strategy twice"
    assert all(b.source == "prov" for b in strat.bars)


async def test_ticks_still_update_last_tick_when_provider_pushes_bars():
    eng = SignalEngine(ReplayProvider(_alpaca_like_stream(1), pushes_bars=True), [Recorder(["AAPL"])], warmup=False)
    await eng.run()
    assert "AAPL" in eng.last_tick


async def test_stream_failure_stops_engine_and_logs(caplog):
    class Broken(ReplayProvider):
        async def stream(self):
            for ev in self._events[:1]:
                yield ev
            raise RuntimeError("socket closed")

    eng = SignalEngine(Broken(ticks("AAPL", [1, 2, 3])), [Recorder(["AAPL"])], warmup=False)
    with caplog.at_level(logging.ERROR, logger="tradesignals.engine"):
        await asyncio.wait_for(eng.run(), timeout=5)  # must not hang
    assert any("data stream failed" in r.getMessage() for r in caplog.records)


async def test_order_update_failure_does_not_stop_trading(caplog):
    class BrokenUpdates(PaperBroker):
        async def order_updates(self):
            raise RuntimeError("updates socket closed")
            yield  # pragma: no cover  (makes this an async generator)

    broker = BrokenUpdates()
    risk = RiskManager(RiskLimits(max_order_notional=10_000, max_quote_age_sec=10**9))
    eng = SignalEngine(
        ReplayProvider(ticks("AAPL", wave(120))),
        [SmaCross(["AAPL"], fast=5, slow=15)],
        broker=broker,
        risk=risk,
        execute=True,
        default_qty=1,
        warmup=False,
        on_tick=lambda t: broker.mark(t.symbol, t.price),
    )
    with caplog.at_level(logging.ERROR, logger="tradesignals.engine"):
        await asyncio.wait_for(eng.run(), timeout=5)
    assert eng.orders, "trading should continue without push updates"
    assert any("order update stream failed" in r.getMessage() for r in caplog.records)


async def test_kill_switch_stops_even_if_cancel_all_fails():
    class Explosive(PaperBroker):
        async def cancel_all(self):
            raise RuntimeError("boom")

    broker = Explosive()
    risk = RiskManager(RiskLimits())
    eng = SignalEngine(ReplayProvider([]), [Recorder(["AAPL"])], broker=broker, risk=risk, execute=True, warmup=False)
    with pytest.raises(RuntimeError):
        await eng.kill_switch()
    assert eng._stop.is_set()
    assert risk.halted and risk.halt_reason == "kill switch"


async def test_run_closes_broker_even_if_data_close_fails():
    class BadClose(ReplayProvider):
        async def close(self):
            raise RuntimeError("close failed")

    class Tracking(PaperBroker):
        closed = False

        async def close(self):
            self.closed = True

    broker = Tracking()
    eng = SignalEngine(BadClose([]), [Recorder(["AAPL"])], broker=broker, warmup=False)
    with pytest.raises(RuntimeError):
        await eng.run()
    assert broker.closed


async def test_execute_without_broker_is_signal_only():
    eng = SignalEngine(
        ReplayProvider(ticks("AAPL", wave(120))), [SmaCross(["AAPL"], fast=5, slow=15)], execute=True, warmup=False
    )
    assert eng.execute is False
    await eng.run()
    assert eng.signals and eng.orders == []


async def test_on_tick_callback_sees_every_tick():
    seen: list[float] = []
    eng = SignalEngine(
        ReplayProvider(ticks("AAPL", [1, 2, 3])),
        [Recorder(["AAPL"])],
        warmup=False,
        on_tick=lambda t: seen.append(t.price),
    )
    await eng.run()
    assert seen == [1, 2, 3]


async def test_warmup_shortfall_is_logged_as_warning(caplog):
    hist = bars("AAPL", [1.0, 2.0, 3.0], start=now_floor() - timedelta(minutes=3))
    strat = SmaCross(["AAPL"], fast=5, slow=15)  # wants 16 bars
    eng = SignalEngine(ReplayProvider([], history={"AAPL": hist}), [strat], warmup=True)
    with caplog.at_level(logging.WARNING, logger="tradesignals.engine"):
        await eng.run()
    assert any("starts cold" in r.getMessage() for r in caplog.records)
    assert not strat._slow["AAPL"].ready


async def test_warmup_failure_is_a_warning_not_a_crash(caplog):
    class NoHistory(ReplayProvider):
        async def get_bars(self, *a, **k):
            raise RuntimeError("history endpoint down")

    strat = SmaCross(["AAPL"], fast=5, slow=15)
    eng = SignalEngine(NoHistory([]), [strat], warmup=True)
    with caplog.at_level(logging.WARNING, logger="tradesignals.engine"):
        await eng.run()
    assert any("warmup sma_cross/AAPL failed" in r.getMessage() for r in caplog.records)
