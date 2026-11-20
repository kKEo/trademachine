"""Markets: indicator series, resampling, bar history and the chart API (Phase 2 of the console)."""

from datetime import timedelta

import pytest
from console_helpers import Rig, finished_run
from helpers import T0, bars, now_floor, ticks, wave

from tradesignals import analytics
from tradesignals.console.api import parse_periods
from tradesignals.console.service import chart_markers
from tradesignals.indicators import EMA, RSI, SMA
from tradesignals.models import Bar, Timeframe
from tradesignals.providers.fake import ReplayProvider
from tradesignals.strategies import SmaCross


# ---------------------------------------------------------------------- analytics
def test_indicator_series_equal_the_incremental_indicators():
    """A line on the chart must be exactly what a strategy computes bar by bar."""
    series = bars("AAPL", wave(80))
    out = analytics.indicator_series(series, sma=[5, 15], ema=[20], rsi=[14])
    sma, ema, rsi = SMA(15), EMA(20), RSI(14)
    exp_sma, exp_ema, exp_rsi = [], [], []
    for i, b in enumerate(series):
        t = int(b.start.timestamp())
        if (v := sma.update(b.close)) is not None:
            exp_sma.append({"time": t, "value": v})
        v = ema.update(b.close)
        if i >= 19:
            exp_ema.append({"time": t, "value": v})
        if (v := rsi.update(b.close)) is not None:
            exp_rsi.append({"time": t, "value": v})
    assert out["sma15"] == exp_sma and len(exp_sma) == 80 - 14
    assert out["ema20"] == exp_ema
    assert out["rsi14"] == exp_rsi and len(exp_rsi) == 80 - 14
    assert set(out) == {"sma5", "sma15", "ema20", "rsi14"}


def test_resample_rolls_one_minute_bars_into_five_minute_bars():
    one = [
        Bar("AAPL", Timeframe.M1, c, c + 1, c - 1, c + 0.5, 10.0, T0 + timedelta(minutes=i), "t")
        for i, c in enumerate([10.0, 11, 12, 13, 14, 20, 21])
    ]
    five = analytics.resample(one, Timeframe.M5)
    assert [b.start for b in five] == [T0, T0 + timedelta(minutes=5)]
    first = five[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (10.0, 15.0, 9.0, 14.5, 50.0)
    assert five[1].close == 21.5  # the bucket in progress is included
    assert analytics.resample(one, Timeframe.M1) == one and analytics.resample([], Timeframe.M5) == []


def test_merge_bars_later_series_wins_and_result_is_sorted():
    a = bars("AAPL", [1.0, 2.0, 3.0])
    b = bars("AAPL", [9.0], start=T0 + timedelta(minutes=1))
    assert [x.close for x in analytics.merge_bars(a, b)] == [1.0, 9.0, 3.0]


def test_trend_state_rising_falling_and_not_enough_bars():
    up = analytics.trend_state(bars("AAPL", [100 + i for i in range(40)]))
    assert (
        up["trend"] == "up"
        and up["spread_pct"] > 0
        and up["rsi_zone"] == "overbought"
        and round(up["change_pct"], 6) == 39
    )
    down = analytics.trend_state(bars("AAPL", [100 - i for i in range(40)]))
    assert down["trend"] == "down" and down["rsi_zone"] == "oversold"
    few = analytics.trend_state(bars("AAPL", [100.0, 101.0]))
    assert few["trend"] is None and few["rsi"] is None and few["last"] == 101.0
    assert analytics.trend_state([])["last"] is None


def test_sparkline_points_stay_inside_the_box():
    pts = [tuple(map(float, p.split(","))) for p in analytics.sparkline([1, 5, 3], width=120, height=28).split()]
    assert pts[0] == (2.0, 26.0) and pts[1] == (60.0, 2.0) and pts[-1][0] == 118.0
    assert analytics.sparkline([1]) == "" and analytics.sparkline([2, 2])  # a flat line still draws


def test_chart_markers_snap_to_the_bar_and_skip_fills_after_the_window():
    t = T0.timestamp()
    signals = [{"ts": t + 290, "kind": "long", "strategy": "sma_cross", "reason": "x", "id": "s1"}]
    fills = [
        {"ts": t + 301, "side": "buy", "filled_qty": 3.0, "avg_fill_price": 101.5, "id": "o1"},
        {"ts": t + 9000, "side": "sell", "filled_qty": 3.0, "avg_fill_price": None, "id": "o2"},
        # stamped far in the future (a replay), but caused by a signal inside the window
        {"ts": t + 9000, "signal_ts": t + 10, "side": "sell", "filled_qty": 1.0, "avg_fill_price": 99.0, "id": "o3"},
    ]
    out = chart_markers(signals, fills, Timeframe.M5, until=t + 600)
    assert [(m["type"], m["time"], m["id"]) for m in out] == [
        ("fill", int(t), "o3"),
        ("signal", int(t), "s1"),
        ("fill", int(t) + 300, "o1"),
    ]
    assert out[2]["text"] == "buy 3" and out[2]["detail"] == "buy 3 @ 101.50"


# ---------------------------------------------------------------------- bar history
def history_rig(tf=Timeframe.M1, n=200, **kw):
    """Provider history ending now, plus a short live replay continuing it."""
    end = now_floor(tf.seconds)
    hist = bars("AAPL", wave(n), tf=tf, start=end - n * timedelta(seconds=tf.seconds))
    live = ticks("AAPL", [100.0 + i for i in range(10)], start=end)
    rig = Rig(events=live, strategies=[SmaCross(["AAPL"], fast=5, slow=15)], **kw)
    rig.data._history = {"AAPL": hist}
    return rig, hist


async def test_bars_backfill_from_the_provider_once_and_are_stored():
    rig, hist = history_rig()
    calls = []
    original = rig.data.get_bars

    async def counting(*a, **k):
        calls.append(a)
        return await original(*a, **k)

    rig.data.get_bars = counting
    got, note = await rig.service.bars("AAPL", Timeframe.M1, limit=500)
    assert note is None and [b.start for b in got] == [b.start for b in hist]
    await rig.service.bars("AAPL", Timeframe.M1, limit=500)
    assert len(calls) == 1  # the second request is served from the database
    assert rig.store.counts()["bars"] == len(hist)


async def test_bars_join_history_with_live_bars_without_duplicates():
    rig, hist = history_rig()
    await rig.run_engine()
    got, _ = await rig.service.bars("AAPL", Timeframe.M1, limit=500)
    starts = [b.start for b in got]
    assert starts == sorted(set(starts))  # de-duplicated by start, oldest first
    assert len(got) == len(hist) + 9  # ten live ticks close nine one-minute bars
    assert got[-1].close == 108.0
    await rig.close()


async def test_coarser_chart_is_rolled_up_from_live_finer_bars():
    """The strategy runs on 1m; a 5m chart must still reach the present."""
    end = now_floor(300)
    live = ticks("AAPL", [100.0 + i for i in range(12)], start=end)
    rig = Rig(events=live)
    await rig.run_engine()
    got, _ = await rig.service.bars("AAPL", Timeframe.M5, limit=100)
    assert [b.timeframe for b in got] == [Timeframe.M5] * 3
    assert (got[0].open, got[0].close) == (100.0, 104.0) and got[1].close == 109.0 and got[2].close == 110.0
    await rig.close()


async def test_history_failure_degrades_to_a_note():
    rig, _ = history_rig()

    async def boom(*a, **k):
        raise RuntimeError("rate limited")

    rig.data.get_bars = boom
    got, note = await rig.service.bars("AAPL", Timeframe.M1)
    assert got == [] and "rate limited" in note and "replay" in note


# ---------------------------------------------------------------------- API and pages
async def test_api_bars_returns_bars_indicators_and_markers():
    async with finished_run() as rig, rig.client() as c:
        d = (await c.get("/api/bars/AAPL")).json()
        assert d["timeframe"] == "1m" and d["seconds"] == 60 and len(d["bars"]) == 119
        assert set(d["bars"][0]) == {"symbol", "timeframe", "time", "open", "high", "low", "close", "volume"}
        assert set(d["indicators"]) == {"sma10", "sma30", "ema20", "rsi14"} and len(d["indicators"]["sma30"]) == 90
        kinds = {m["type"] for m in d["markers"]}
        assert kinds == {"signal", "fill"}
        assert len([m for m in d["markers"] if m["type"] == "signal"]) == len(rig.engine.signals)
        times = {b["time"] for b in d["bars"]}
        assert all(m["time"] in times for m in d["markers"])  # every marker sits on a bar


async def test_api_bars_pagination_with_limit_and_before():
    async with finished_run() as rig, rig.client() as c:
        newest = (await c.get("/api/bars/AAPL", params={"limit": 50})).json()["bars"]
        assert len(newest) == 50
        older = (await c.get("/api/bars/AAPL", params={"limit": 50, "before": newest[0]["time"]})).json()["bars"]
        assert len(older) == 50 and older[-1]["time"] == newest[0]["time"] - 60
        rest = (await c.get("/api/bars/AAPL", params={"limit": 50, "before": older[0]["time"]})).json()["bars"]
        assert len(rest) == 19  # 119 bars in total


async def test_api_bars_custom_periods_and_validation():
    async with finished_run() as rig, rig.client() as c:
        d = (await c.get("/api/bars/aapl", params={"sma": "7", "ema": "", "rsi": ""})).json()
        assert set(d["indicators"]) == {"sma7"}
        assert (await c.get("/api/bars/AAPL", params={"tf": "9m"})).status_code == 422
        assert (await c.get("/api/bars/AAPL", params={"sma": "abc"})).status_code == 422
        assert (await c.get("/api/bars/AAPL", params={"sma": "1"})).status_code == 422
        assert (await c.get("/api/bars/NOPE")).status_code == 404


def test_parse_periods():
    assert (
        parse_periods(None, (10, 30)) == (10, 30)
        and parse_periods("", (10,)) == ()
        and parse_periods("5, 9", ()) == (5, 9)
    )


@pytest.mark.parametrize("path", ["/markets", "/markets/AAPL", "/markets/AAPL?tf=15m", "/partials/watchlist"])
async def test_market_pages_render(path):
    async with finished_run() as rig, rig.client() as c:
        r = await c.get(path)
        assert r.status_code == 200 and "AAPL" in r.text


async def test_watchlist_reports_trend_and_strategy():
    prices = [100.0] * 20 + [100.0 + i for i in range(1, 41)]  # flat, then rising: one golden cross
    async with finished_run(events=ticks("AAPL", prices)) as rig, rig.client() as c:
        [row] = (await c.get("/api/markets")).json()
        assert row["symbol"] == "AAPL" and row["timeframe"] == "1m" and row["strategies"] == ["sma_cross"]
        assert row["state"]["trend"] == "up" and row["spark"] and row["position"]["qty"] == 3
        html = (await c.get("/markets")).text
        assert "Rising" in html and "<polyline" in html
        assert (await c.get("/markets/NOPE")).status_code == 404


async def test_replay_provider_history_is_filtered_by_timeframe():
    hist = bars("AAPL", [1.0, 2.0], tf=Timeframe.M5)
    data = ReplayProvider([], history={"AAPL": hist})
    assert await data.get_bars("AAPL", Timeframe.M1, T0) == []
