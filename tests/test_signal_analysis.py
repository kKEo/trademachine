"""Signal and trade analytics on fixtures with known answers, and the Signals pages (Phase 3)."""

import csv
import io

import pytest
from console_helpers import finished_run
from helpers import T0, ticks

from tradesignals import analytics
from tradesignals.store import MIGRATIONS, Store

T = T0.timestamp()


def fill(side, qty, price, minute, symbol="AAPL", **extra):
    return {"symbol": symbol, "side": side, "filled_qty": qty, "avg_fill_price": price, "ts": T + minute * 60, **extra}


# ---------------------------------------------------------------------- round trips
def test_round_trip_simple_long_win():
    [t] = analytics.round_trips([fill("buy", 10, 100.0, 0, strategy="sma_cross"), fill("sell", 10, 103.0, 5)])
    assert (t["direction"], t["qty"], t["entry_price"], t["exit_price"]) == ("long", 10, 100.0, 103.0)
    assert t["pnl"] == pytest.approx(30.0) and t["pnl_pct"] == pytest.approx(3.0)
    assert t["open"] is False and t["hold_sec"] == 300 and t["strategy"] == "sma_cross"


def test_round_trip_adds_average_the_entry_and_partial_exits_realise_pnl():
    trips = analytics.round_trips(
        [
            fill("buy", 10, 100.0, 0),
            fill("buy", 10, 110.0, 1),  # average cost 105
            fill("sell", 5, 115.0, 2),  # +50 realised, 15 left
            fill("sell", 15, 100.0, 3),  # -75
        ]
    )
    [t] = trips
    assert t["qty"] == 20 and t["entry_price"] == pytest.approx(105.0)
    assert t["exit_price"] == pytest.approx((5 * 115 + 15 * 100) / 20)
    assert t["pnl"] == pytest.approx(50 - 75) and t["open"] is False and t["fills"] == 4


def test_round_trip_partial_exit_leaves_the_trip_open_with_realised_pnl():
    [t] = analytics.round_trips([fill("buy", 10, 100.0, 0), fill("sell", 4, 110.0, 1)])
    assert t["open"] is True and t["exit_ts"] is None and t["open_qty"] == 6
    assert t["pnl"] == pytest.approx(40.0) and t["hold_sec"] is None


def test_round_trip_flip_through_zero_closes_one_trip_and_opens_the_next():
    long_trip, short_trip = analytics.round_trips(
        [fill("buy", 10, 100.0, 0), fill("sell", 15, 110.0, 1), fill("buy", 5, 104.0, 2)]
    )
    assert long_trip["direction"] == "long" and long_trip["pnl"] == pytest.approx(100.0) and not long_trip["open"]
    assert short_trip["direction"] == "short" and short_trip["qty"] == 5 and short_trip["entry_price"] == 110.0
    assert short_trip["pnl"] == pytest.approx(30.0) and not short_trip["open"]  # sold 110, bought back 104
    assert short_trip["entry_ts"] == long_trip["exit_ts"]


def test_round_trips_keep_symbols_apart_skip_unpriced_fills_and_use_market_time():
    trips = analytics.round_trips(
        [
            fill("buy", 1, 10.0, 0, symbol="AAPL"),
            fill("buy", 1, 50.0, 1, symbol="MSFT", origin="rsi_reversion:long"),
            fill("sell", 1, None, 2, symbol="AAPL"),  # no fill price: ignored
            fill("sell", 1, 12.0, 999, symbol="AAPL", signal_ts=T + 180),  # replay: wall clock is far off
        ]
    )
    assert [(t["symbol"], t["open"]) for t in trips] == [("AAPL", False), ("MSFT", True)]
    assert trips[0]["exit_ts"] == T + 180 and trips[0]["hold_sec"] == 180
    assert trips[0]["strategy"] == "manual" and trips[1]["strategy"] == "rsi_reversion"
    assert analytics.round_trips([]) == []


def test_trip_stats_and_groups():
    trips = analytics.round_trips(
        [
            fill("buy", 10, 100.0, 0, strategy="a"),
            fill("sell", 10, 103.0, 1),  # +30
            fill("buy", 10, 100.0, 2, strategy="a"),
            fill("sell", 10, 99.0, 4),  # -10
            fill("buy", 1, 50.0, 0, symbol="MSFT", strategy="b"),
            fill("sell", 1, 60.0, 10, symbol="MSFT"),  # +10
            fill("buy", 2, 20.0, 0, symbol="KO", strategy="b"),  # still open
        ]
    )
    st = analytics.trip_stats(trips)
    assert (st["trades"], st["open"], st["wins"], st["losses"]) == (3, 1, 2, 1)
    assert st["win_rate"] == pytest.approx(200 / 3) and st["pnl"] == pytest.approx(30.0)
    assert st["avg_win"] == pytest.approx(20.0) and st["avg_loss"] == pytest.approx(-10.0)
    assert st["profit_factor"] == pytest.approx(4.0) and (st["best"], st["worst"]) == (
        pytest.approx(30),
        pytest.approx(-10),
    )
    assert st["avg_hold_sec"] == pytest.approx((60 + 120 + 600) / 3)
    by = analytics.group_stats(trips, "strategy")
    assert by["a"]["pnl"] == pytest.approx(20.0) and by["b"]["trades"] == 1 and by["b"]["open"] == 1
    assert set(analytics.group_stats(trips, "symbol")) == {"AAPL", "KO", "MSFT"}
    empty = analytics.trip_stats([])
    assert empty["trades"] == 0 and empty["win_rate"] is None and empty["profit_factor"] is None and empty["pnl"] == 0


def test_max_drawdown():
    assert analytics.max_drawdown([100, 110, 99, 104.5]) == {
        "max_pct": pytest.approx(-10.0),
        "current_pct": pytest.approx(-5.0),
    }
    assert analytics.max_drawdown([]) == {"max_pct": None, "current_pct": None}


# ---------------------------------------------------------------------- forward returns
TIMES = [T + i * 60 for i in range(8)]
CLOSES = [100.0, 101.0, 102.0, 100.0, 99.0, 105.0, 110.0, 90.0]


def test_forward_returns_measure_from_the_signal_bar_close():
    f = analytics.forward_returns("long", T + 60, TIMES, CLOSES, horizons=(1, 5, 15))
    assert f["ref"] == 101.0
    assert f["returns"][1] == pytest.approx(100 * (102 / 101 - 1)) and f["returns"][5] == pytest.approx(
        100 * (110 / 101 - 1)
    )
    assert f["returns"][15] is None and f["edge"][15] is None  # not enough bars yet
    assert f["edge"][1] == f["returns"][1]  # a long is right when the price rises


def test_forward_returns_edge_is_inverted_for_exits_and_shorts():
    flat = analytics.forward_returns("flat", T + 120, TIMES, CLOSES, horizons=(1, 2))
    assert flat["returns"][1] == pytest.approx(100 * (100 / 102 - 1)) and flat["edge"][1] == pytest.approx(
        -flat["returns"][1]
    )
    assert flat["edge"][2] > 0  # the price fell after the exit: the exit was right
    assert analytics.forward_returns("short", T + 120, TIMES, CLOSES, horizons=(1,))["edge"][1] > 0


def test_forward_returns_snap_to_the_last_bar_at_or_before_the_signal():
    assert analytics.forward_returns("long", T + 90, TIMES, CLOSES, horizons=(1,))["ref"] == 101.0
    before = analytics.forward_returns("long", T - 1, TIMES, CLOSES, horizons=(1,))
    assert before == {"ref": None, "returns": {1: None}, "edge": {1: None}}
    assert analytics.forward_returns("long", T, [], [], horizons=(1,))["ref"] is None


def test_signal_stats_per_strategy():
    rows = [
        {"strategy": "a", "kind": "long", "outcome": "order", "edge": {1: 1.0, 5: -2.0}},
        {"strategy": "a", "kind": "flat", "outcome": "risk_reject", "edge": {1: 3.0, 5: None}},
        {"strategy": "a", "kind": "long", "outcome": None, "edge": {1: -1.0, 5: None}},
        {"strategy": "b", "kind": "long", "outcome": "order"},
    ]
    st = analytics.signal_stats(rows, horizons=(1, 5))
    a = st["a"]
    assert a["signals"] == 3 and a["kinds"] == {"long": 2, "flat": 1}
    assert a["outcomes"] == {"order": 1, "risk_reject": 1, "none": 1}
    assert (
        a["hit_rate"][1] == pytest.approx(200 / 3)
        and a["avg_edge"][1] == pytest.approx(1.0)
        and a["measured"] == {1: 3, 5: 1}
    )
    assert a["hit_rate"][5] == 0 and a["avg_edge"][5] == -2.0
    assert st["b"]["hit_rate"] == {1: None, 5: None} and list(st) == ["a", "b"]


def test_execution_quality_slippage_and_latency():
    q = analytics.execution_quality("buy", 101.0, 100.0, signal_ts=T, order_ts=T + 61.5, bar_seconds=60)
    assert q["slippage_pct"] == pytest.approx(1.0) and q["latency_sec"] == pytest.approx(1.5)
    sell = analytics.execution_quality("sell", 101.0, 100.0, T, T + 60, 60)
    assert sell["slippage_pct"] == pytest.approx(-1.0)  # sold above the reference: better
    replay = analytics.execution_quality("buy", None, 100.0, T, T + 10**6, 60)
    assert replay == {"slippage_pct": None, "latency_sec": None}


# ---------------------------------------------------------------------- store
def test_store_filters_signals_by_outcome_and_migrates_old_files(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)  # a file from before the timeframe column existed
    db.execute(
        "CREATE TABLE signals (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ts REAL NOT NULL, symbol TEXT NOT NULL, "
        "kind TEXT NOT NULL, strategy TEXT NOT NULL, strength REAL NOT NULL, reason TEXT NOT NULL DEFAULT '')"
    )
    db.execute("INSERT INTO signals VALUES ('old', 'r', 1.0, 'AAPL', 'long', 'sma_cross', 1.0, '')")
    db.commit()
    db.close()
    store = Store(path)
    assert MIGRATIONS and store.signal("old")["timeframe"] == ""
    assert [s["id"] for s in store.signals(outcome="none")] == ["old"] and store.signals(outcome="order") == []
    assert store.signal_facets() == {"strategies": ["sma_cross"], "symbols": ["AAPL"]}
    assert store.signal("missing") is None


# ---------------------------------------------------------------------- service, API and pages
# flat, a rise (golden cross -> buy), a fall (death cross -> sell), then flat again
PRICES = [100.0] * 20 + [100.0 + i for i in range(1, 21)] + [120.0 - 2 * i for i in range(1, 21)] + [80.0] * 20


async def test_signal_report_and_detail_answer_what_happened_next():
    async with finished_run(events=ticks("AAPL", PRICES)) as rig, rig.client() as c:
        report = (await c.get("/api/signals/report")).json()
        assert report["count"] == len(rig.engine.signals) == 2 and report["outcomes"] == {"order": 2}
        flat, long = report["signals"]  # newest first
        assert (long["kind"], long["timeframe"], flat["kind"]) == ("long", "1m", "flat")
        assert long["ref"] is not None and long["returns"]["5"] > 0 and long["edge"]["5"] > 0  # it kept rising
        assert flat["returns"]["5"] < 0 and flat["edge"]["5"] > 0  # it kept falling after the exit
        strat = report["by_strategy"]["sma_cross"]
        assert strat["signals"] == 2 and strat["hit_rate"]["5"] == 100 and strat["trades"]["trades"] == 1

        detail = (await c.get(f"/api/signals/{long['id']}")).json()
        assert detail["order"]["side"] == "buy" and detail["order"]["filled_qty"] == 3
        assert detail["trip"]["role"] == "opened" and detail["trip"]["open"] is False
        assert detail["quality"]["slippage_pct"] is not None and detail["quality"]["latency_sec"] is None  # a replay
        assert [o["kind"] for o in detail["outcomes"]] == ["order"] and detail["order_updates"]
        closing = (await c.get(f"/api/signals/{flat['id']}")).json()
        assert closing["trip"]["role"] == "closed" and closing["trip"]["pnl"] == pytest.approx(detail["trip"]["pnl"])
        assert (await c.get("/api/signals/nope")).status_code == 404


async def test_trade_report_matches_the_broker_cash():
    async with finished_run(events=ticks("AAPL", PRICES)) as rig, rig.client() as c:
        report = (await c.get("/api/trades")).json()
        [trip] = report["trips"]
        assert trip["qty"] == 3 and trip["strategy"] == "sma_cross" and not trip["open"]
        # realised P&L from round trips must equal what the paper broker's cash says
        assert report["stats"]["pnl"] == pytest.approx(rig.broker.cash - 100_000.0)
        assert set(report["by_symbol"]) == {"AAPL"} and set(report["by_strategy"]) == {"sma_cross"}


async def test_signals_page_filters_and_empty_states():
    async with finished_run(events=ticks("AAPL", PRICES)) as rig, rig.client() as c:
        html = (await c.get("/signals")).text
        assert "By strategy" in html and "Order sent" in html and "Download CSV" in html
        only_long = (await c.get("/signals", params={"kind": "long"})).text
        assert "Matching signals" in only_long and 'href="/signals.csv?kind=long"' in only_long
        assert only_long.count('class="kind kind-long"') == 1 and 'class="kind kind-flat"' not in only_long
        none = (await c.get("/signals", params={"outcome": "risk_reject"})).text
        assert "No signals match these filters" in none
        assert (await c.get("/signals", params={"scope": "run", "symbol": "aapl"})).status_code == 200
        assert (await c.get("/signals", params={"day_to": "2020-01-01"})).text.count("No signals match") == 1
        for bad in ({"kind": "sideways"}, {"outcome": "maybe"}, {"day_from": "yesterday"}):
            assert (await c.get("/signals", params=bad)).status_code == 422
        assert (await c.get("/partials/signal-report")).status_code == 200


async def test_signal_detail_page_and_portfolio_trades_render():
    async with finished_run(events=ticks("AAPL", PRICES)) as rig, rig.client() as c:
        sid = rig.store.signals(kind="long")[0]["id"]
        html = (await c.get(f"/signals/{sid}")).text
        assert "Price at the signal" in html and "Trade it opened" in html and "Slippage" in html
        assert f'data-focus="{int(rig.store.signal(sid)["ts"])}"' in html
        assert (await c.get("/signals/nope")).status_code == 404
        portfolio = (await c.get("/portfolio")).text
        assert "Trade history" in portfolio and "Realised P&amp;L" in portfolio and "Profit factor" in portfolio
        assert (await c.get("/partials/trades")).status_code == 200


async def test_signal_only_run_shows_untraded_signals_with_forward_returns():
    async with finished_run(events=ticks("AAPL", PRICES), broker=False, execute=False) as rig, rig.client() as c:
        report = (await c.get("/api/signals/report")).json()
        assert report["outcomes"] == {"none": 2} and report["signals"][1]["edge"]["5"] > 0
        sid = report["signals"][0]["id"]
        assert "signals-only mode" in (await c.get(f"/signals/{sid}")).text
        assert "No trades yet" in (await c.get("/portfolio")).text


async def test_signals_csv_export():
    async with finished_run(events=ticks("AAPL", PRICES)) as rig, rig.client() as c:
        r = await c.get("/signals.csv", params={"kind": "long"})
        assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert len(rows) == 1 and rows[0]["symbol"] == "AAPL" and rows[0]["signal"] == "long"
        assert rows[0]["result"] == "order" and rows[0]["time_utc"].endswith("Z")
        assert float(rows[0]["move_pct_after_5_bars"]) > 0 and rows[0]["timeframe"] == "1m"
        assert len(list(csv.DictReader(io.StringIO((await c.get("/signals.csv")).text)))) == 2
