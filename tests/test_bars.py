from datetime import timedelta

from helpers import T0, bars, ticks

from tradesignals.bars import BarAggregator, bucket_start
from tradesignals.models import Tick, Timeframe


def test_bucket_start_floors_to_utc_multiples():
    ts = T0 + timedelta(minutes=7, seconds=31)  # 14:37:31 UTC
    assert bucket_start(ts, Timeframe.M1) == T0 + timedelta(minutes=7)
    assert bucket_start(ts, Timeframe.M5) == T0 + timedelta(minutes=5)
    assert bucket_start(ts, Timeframe.H1) == T0.replace(minute=0)  # on the UTC hour, not the US open
    assert bucket_start(ts, Timeframe.D1) == T0.replace(hour=0, minute=0)


def test_late_tick_is_dropped_instead_of_resurrecting_old_bucket():
    agg = BarAggregator("AAPL", Timeframe.M1)
    first, second = ticks("AAPL", [10, 11], step_sec=60)  # 14:30:00 and 14:31:00
    assert agg.on_tick(first) is None
    completed = agg.on_tick(second)
    assert completed is not None and completed.start == T0  # 14:30 bar emitted on roll-over

    late = Tick("AAPL", 8.99, 9.01, T0 + timedelta(seconds=50), "test")  # belongs to the closed 14:30 bucket
    assert agg.on_tick(late) is None
    assert agg.dropped_late == 1
    assert agg.current is not None and agg.current.start == T0 + timedelta(minutes=1)  # 14:31 untouched
    assert agg.current.close == 11

    out = agg.on_tick(Tick("AAPL", 11.99, 12.01, T0 + timedelta(minutes=2), "test"))
    assert out is not None and out.start == T0 + timedelta(minutes=1)  # the 14:31 bar, not a second 14:30


def test_provider_bars_roll_into_coarser_timeframe():
    agg = BarAggregator("AAPL", Timeframe.M5)
    m1 = bars("AAPL", [1, 2, 3, 4, 5, 6], tf=Timeframe.M1)  # 14:30 .. 14:35
    done = [b for b in (agg.on_bar(b) for b in m1) if b]
    assert len(done) == 1
    assert done[0].start == T0
    assert (done[0].open, done[0].high, done[0].low, done[0].close) == (1, 5, 1, 5)
    assert agg.current is not None and agg.current.start == T0 + timedelta(minutes=5)


def test_same_timeframe_bar_passes_through_unchanged():
    agg = BarAggregator("AAPL", Timeframe.M1)
    b = bars("AAPL", [42.0])[0]
    assert agg.on_bar(b) is b
    assert agg.current is None


def test_tick_built_bars_have_zero_volume_and_mid_prices():
    agg = BarAggregator("AAPL", Timeframe.M1)
    agg.on_tick(Tick("AAPL", 99.0, 101.0, T0, "test"))
    cur = agg.current
    assert cur is not None and cur.open == 100.0 and cur.volume == 0.0
