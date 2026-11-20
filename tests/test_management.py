"""Full management from the console: manual orders, strategies, risk limits (Phase 5)."""

from datetime import timedelta

import pytest
from console_helpers import Rig, finished_run
from helpers import T0, bars, now_floor, ticks
from test_controls import AUTH, HOLDING, TOKEN, live_engine

from tradesignals.console.service import strategy_catalog, strategy_params
from tradesignals.engine import ActionRefused, SignalEngine
from tradesignals.events import BarEvent, EventBus, OutcomeKind
from tradesignals.models import Account, Bar, OrderRequest, Side, Signal, SignalKind, Timeframe
from tradesignals.providers.fake import ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.strategies import RsiReversion, SmaCross, Strategy


class Recorder(Strategy):
    """Remembers every bar it is given; signals LONG on each when asked to."""

    name = "recorder"

    def __init__(self, symbols, timeframe=Timeframe.M1, speak=False):
        super().__init__(symbols)
        self.timeframe = timeframe
        self.speak = speak
        self.seen: list[Bar] = []

    def on_bar(self, bar):
        self.seen.append(bar)
        return Signal(bar.symbol, SignalKind.LONG, self.name, bar.start) if self.speak else None


# ---------------------------------------------------------------------- shared aggregators (was gap M5)
async def test_two_strategies_on_one_symbol_and_timeframe_both_get_every_bar():
    a, b = Recorder(["AAPL"]), Recorder(["AAPL"])
    bus = EventBus()
    published = bus.subscribe(types=(BarEvent,), maxsize=1000)
    eng = SignalEngine(ReplayProvider(ticks("AAPL", [float(i) for i in range(1, 11)])), [a, b], warmup=False, bus=bus)
    await eng.run()
    assert len(a.seen) == len(b.seen) == 9
    assert [x.close for x in a.seen] == [x.close for x in b.seen]
    assert sum(1 for _ in iter(published.get_nowait, None)) == 9  # each bar is published once, not per strategy


async def test_rolled_up_volume_is_counted_once_however_many_strategies_share_the_bar():
    one_minute = [Bar("AAPL", Timeframe.M1, 10, 11, 9, 10, 100.0, T0.replace(minute=30 + i), "t") for i in range(6)]
    a, b = Recorder(["AAPL"], Timeframe.M5), Recorder(["AAPL"], Timeframe.M5)
    c = Recorder(["AAPL"], Timeframe.M1)
    eng = SignalEngine(ReplayProvider(one_minute, pushes_bars=True), [a, b, c], warmup=False)
    await eng.run()
    assert [x.volume for x in a.seen] == [500.0] == [x.volume for x in b.seen]  # 5 x 100, not doubled
    assert len(c.seen) == 6  # the 1m strategy is unaffected by the 5m ones


# ---------------------------------------------------------------------- strategies at run time
async def test_strategy_ids_pause_resume_and_remove():
    a, b = Recorder(["AAPL"], speak=True), Recorder(["AAPL"], speak=True)
    eng = SignalEngine(ReplayProvider([]), [a, b], warmup=False)
    assert [sid for sid, _, _ in eng.strategy_states()] == ["recorder-1", "recorder-2"]
    assert eng.strategy_id(b) == "recorder-2"
    [bar] = bars("AAPL", [1.0])
    eng.pause_strategy("recorder-1")
    await eng._deliver(bar)
    assert len(a.seen) == len(b.seen) == 1  # the paused strategy still sees the bar...
    assert len(eng.signals) == 1  # ...but only the other one's signal counts
    eng.resume_strategy("recorder-1")
    await eng._deliver(bar)
    assert len(eng.signals) == 3
    eng.remove_strategy("recorder-1")
    await eng._deliver(bar)
    assert len(a.seen) == 2 and len(b.seen) == 3 and eng.strategies == [b]
    for call in (eng.pause_strategy, eng.resume_strategy, eng.remove_strategy):
        with pytest.raises(ActionRefused):
            call("recorder-1")


async def test_add_strategy_warms_up_and_only_takes_known_symbols():
    end = now_floor()
    hist = bars("AAPL", [100.0 + i for i in range(60)], start=end - timedelta(minutes=60))
    eng = SignalEngine(ReplayProvider([], history={"AAPL": hist}), [SmaCross(["AAPL"])], warmup=False)
    st = SmaCross(["AAPL"], fast=3, slow=8)
    sid = await eng.add_strategy(st)
    assert sid == "sma_cross-2" and st._slow["AAPL"].ready and eng.strategies[-1] is st
    with pytest.raises(ActionRefused, match="MSFT is not in this session"):
        await eng.add_strategy(SmaCross(["MSFT"]))
    with pytest.raises(ActionRefused, match="at least one symbol"):
        await eng.add_strategy(SmaCross([]))


def test_strategy_catalog_and_params():
    cat = {c["name"]: c for c in strategy_catalog()}
    assert [p["name"] for p in cat["sma_cross"]["params"]] == ["fast", "slow"]
    assert cat["sma_cross"]["params"][0] == {"name": "fast", "default": 10, "whole": True}
    assert [p["name"] for p in cat["rsi_reversion"]["params"]] == ["n", "oversold", "exit"]
    assert cat["rsi_reversion"]["timeframe"] == "5m" and cat["sma_cross"]["about"].startswith("Classic")
    assert strategy_params(SmaCross(["AAPL"], fast=4, slow=9)) == {"fast": 4, "slow": 9}
    assert strategy_params(RsiReversion(["AAPL"], n=7)) == {"n": 7, "oversold": 30, "exit": 55}


# ---------------------------------------------------------------------- manual orders and limits (engine)
async def test_manual_order_passes_and_fails_risk_like_a_strategy_order():
    eng, broker = live_engine(limits=RiskLimits(max_order_notional=500, max_quote_age_sec=10**9))
    order, kind, detail = await eng.submit_manual(OrderRequest("AAPL", Side.BUY, 2))
    assert kind is OutcomeKind.ORDER and order.qty == 2 and broker.positions["AAPL"].qty == 2 and detail == "buy 2"
    order, kind, why = await eng.submit_manual(OrderRequest("AAPL", Side.BUY, 10))  # 1000 > 500
    assert order is None and kind is OutcomeKind.RISK_REJECT and why.startswith("notional")
    eng.halt("stop")
    order, kind, why = await eng.submit_manual(OrderRequest("AAPL", Side.BUY, 1))
    assert order is None and why == "halted: stop"
    order, kind, _ = await eng.submit_manual(OrderRequest("AAPL", Side.SELL, 2))  # an exit always passes
    assert order is not None and broker.positions == {}


async def test_preview_does_not_send_or_latch_anything():
    eng, broker = live_engine(limits=RiskLimits(max_daily_loss=10, max_order_notional=10_000, max_quote_age_sec=10**9))
    broker.cash = 99_000.0  # the account is down 1000 against a baseline that is not set yet
    p = await eng.preview_order(OrderRequest("AAPL", Side.BUY, 2))
    assert p["ok"] and p["notional"] == pytest.approx(200.0) and (p["position_before"], p["position_after"]) == (0, 2)
    assert broker.orders == {} and eng.risk.start_equity is None  # nothing sent, no baseline fixed
    eng.risk.start_equity = 100_000.0
    p = await eng.preview_order(OrderRequest("AAPL", Side.BUY, 2))
    assert not p["ok"] and p["why"].startswith("daily loss") and not eng.halted  # would block, but did not halt


def test_risk_check_commit_false_leaves_state_alone():
    rm = RiskManager(RiskLimits(max_daily_loss=100))
    from tradesignals.models import Tick

    tick = Tick("AAPL", 99.99, 100.01, T0, "t")
    acct = Account(10_000, 10_000, 10_000)
    assert rm.check(OrderRequest("AAPL", Side.BUY, 1), tick, [], acct, now=T0, commit=False)[0]
    assert rm.start_equity is None


async def test_limit_edits_take_effect_on_the_next_check():
    eng, broker = live_engine(limits=RiskLimits(max_order_notional=500, max_quote_age_sec=10**9))
    assert (await eng.submit_manual(OrderRequest("AAPL", Side.BUY, 10)))[0] is None
    changed = eng.set_risk_limits(max_order_notional=2000.0, max_open_positions=5)
    assert changed == {"max_order_notional": (500, 2000.0)}  # the unchanged one is not reported
    assert (await eng.submit_manual(OrderRequest("AAPL", Side.BUY, 10)))[0] is not None
    with pytest.raises(ActionRefused):
        SignalEngine(ReplayProvider([]), [SmaCross(["AAPL"])], warmup=False).set_risk_limits(max_daily_loss=1)


# ---------------------------------------------------------------------- console
def holding(**kw):
    return finished_run(events=ticks("AAPL", HOLDING), token=TOKEN, **kw)


async def test_console_order_ticket_preview_and_send():
    async with holding() as rig, rig.client(headers=AUTH) as c:
        page = (await c.get("/portfolio")).text
        assert "Place an order" in page and "Check against risk" in page
        body = {"symbol": "aapl", "side": "buy", "qty": "2", "type": "market"}
        p = (await c.post("/api/actions/order/preview", json=body)).json()
        assert p["allowed"] is True and "Risk would allow buy 2 AAPL" in p["message"] and "from 3 to 5" in p["message"]
        assert rig.broker.positions["AAPL"].qty == 3 and rig.store.audit_log() == []  # a preview does nothing
        r = await c.post("/api/actions/order", json=body)
        assert r.status_code == 200 and "Order sent: buy 2 AAPL, filled at" in r.json()["message"]
        assert rig.broker.positions["AAPL"].qty == 5
        await rig.flush()
        manual = rig.store.orders(limit=1)[0]
        assert manual["origin"] == "manual" and manual["signal_id"] is None
        assert rig.store.audit_log()[0]["action"] == "order"
        trips = (await c.get("/api/trades")).json()["trips"]
        assert trips[0]["qty"] == 5  # the manual add joins the open round trip


async def test_console_order_blocked_by_risk_and_validated():
    limits = RiskLimits(max_order_notional=400, max_quote_age_sec=10**9)
    async with holding(limits=limits) as rig, rig.client(headers=AUTH) as c:
        big = {"symbol": "AAPL", "side": "buy", "qty": 100}
        p = (await c.post("/api/actions/order/preview", json=big)).json()
        assert p["allowed"] is False and "Risk would block buy 100 AAPL: notional" in p["message"]
        r = await c.post("/api/actions/order", json=big)
        assert r.status_code == 409 and "Risk blocked buy 100 AAPL" in r.json()["error"]
        for bad, word in (
            ({"symbol": "NOPE", "side": "buy", "qty": 1}, "symbols"),
            ({"symbol": "AAPL", "side": "hold", "qty": 1}, "buy or sell"),
            ({"symbol": "AAPL", "side": "buy", "qty": "many"}, "must be a number"),
            ({"symbol": "AAPL", "side": "buy", "qty": 0}, "between"),
            ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "stop"}, "market or a limit"),
            ({"symbol": "AAPL", "side": "buy", "qty": 1, "type": "limit"}, "Limit price"),
        ):
            r = await c.post("/api/actions/order", json=bad)
            assert r.status_code == 400 and word in r.json()["error"], bad
        assert rig.broker.positions["AAPL"].qty == 3


async def test_console_limit_order_that_is_not_marketable_is_reported():
    async with holding() as rig, rig.client(headers=AUTH) as c:
        r = await c.post(
            "/api/actions/order", json={"symbol": "AAPL", "side": "buy", "qty": 1, "type": "limit", "limit_price": 1}
        )
        assert r.status_code == 200 and "rejected" in r.json()["message"]  # the paper broker never rests orders
        assert rig.broker.positions["AAPL"].qty == 3


async def test_console_changes_limits_within_bounds_and_audits_them():
    async with holding() as rig, rig.client(headers=AUTH) as c:
        assert "Change limits" in (await c.get("/risk")).text
        # the form posts every field; the untouched 10**9 quote age must not be rejected as out of range
        form = {row["name"]: row["value"] for row in rig.service.limits_view()}
        r = await c.post("/api/actions/limits", json={**form, "max_order_notional": "750", "max_open_positions": "2"})
        assert r.status_code == 200 and "value of a single order 10000 to 750" in r.json()["message"]
        assert rig.risk.limits.max_order_notional == 750 and rig.risk.limits.max_open_positions == 2
        assert isinstance(rig.risk.limits.max_open_positions, int)
        assert (
            await c.post("/api/actions/limits", json=form | {"max_order_notional": 750, "max_open_positions": 2})
        ).json()["message"] == "No limit was changed."
        for bad in (
            {"max_daily_loss": "0"},
            {"max_open_positions": "2.5"},
            {"max_quote_age_sec": "999999"},
            {"max_order_notional": "lots"},
        ):
            assert (await c.post("/api/actions/limits", json=bad)).status_code == 400
        assert rig.risk.limits.max_daily_loss == 200
        log = rig.store.audit_log()
        assert log[-1]["action"] == "set_limits" and "max_order_notional=750" in log[-1]["detail"]
        # the new cap is in force for the next order
        assert (
            await c.post("/api/actions/order", json={"symbol": "AAPL", "side": "buy", "qty": 10})
        ).status_code == 409


async def test_console_strategy_lifecycle():
    async with holding() as rig, rig.client(headers=AUTH) as c:
        page = (await c.get("/strategies")).text
        assert (
            "sma_cross-1" in page and "Start sma_cross" in page and "Start rsi_reversion" in page and "Trading" in page
        )
        r = await c.post("/api/actions/strategies/pause", json={"id": "sma_cross-1"})
        assert r.status_code == 200 and rig.engine._paused == {"sma_cross-1"}
        assert "Paused" in (await c.get("/partials/strategies")).text
        assert (await c.get("/api/status")).json()["strategies"][0]["paused"] is True
        assert (await c.post("/api/actions/strategies/resume", json={"id": "sma_cross-1"})).status_code == 200

        add = {"strategy": "rsi_reversion", "symbols": ["AAPL"], "timeframe": "1m", "n": "7", "oversold": "25"}
        r = await c.post("/api/actions/strategies/add", json=add)
        assert r.status_code == 200 and "rsi_reversion-1 started on AAPL with 1m bars" in r.json()["message"]
        running = (await c.get("/api/strategies")).json()["running"]
        assert running[1]["params"] == {"n": 7, "oversold": 25.0, "exit": 55} and running[1]["timeframe"] == "1m"

        assert (await c.post("/api/actions/strategies/remove", json={"id": "sma_cross-1"})).status_code == 200
        assert [s["id"] for s in (await c.get("/api/strategies")).json()["running"]] == ["rsi_reversion-1"]
        assert (await c.post("/api/actions/strategies/remove", json={"id": "sma_cross-1"})).status_code == 409
        assert (await c.post("/api/actions/strategies/explode", json={"id": "rsi_reversion-1"})).status_code == 400
        actions = [a["action"] for a in rig.store.audit_log()][::-1]
        assert actions[:4] == ["strategy_pause", "strategy_resume", "strategy_add", "strategy_remove"]
        assert rig.broker.positions["AAPL"].qty == 3  # removing a strategy sells nothing


async def test_console_add_strategy_validation():
    async with holding() as rig, rig.client(headers=AUTH) as c:
        for bad, word in (
            ({"strategy": "nope", "symbols": ["AAPL"]}, "Choose one of"),
            ({"strategy": "sma_cross", "symbols": []}, "at least one symbol"),
            ({"strategy": "sma_cross", "symbols": "AAPL", "fast": 30, "slow": 10}, "shorter than the slow"),
            ({"strategy": "sma_cross", "symbols": "AAPL", "fast": "2.5"}, "whole number"),
            ({"strategy": "sma_cross", "symbols": "AAPL", "timeframe": "7m"}, "Unknown bar size"),
        ):
            r = await c.post("/api/actions/strategies/add", json=bad)
            assert r.status_code == 400 and word in r.json()["error"], bad
        r = await c.post("/api/actions/strategies/add", json={"strategy": "sma_cross", "symbols": "MSFT"})
        assert r.status_code == 409 and "MSFT is not in this session" in r.json()["error"]
        assert len(rig.engine.strategies) == 1


async def test_management_pages_hide_forms_without_access():
    async with finished_run(events=ticks("AAPL", HOLDING)) as rig, rig.client() as c:  # view only
        assert "Place an order" not in (await c.get("/portfolio")).text
        assert "Change limits" not in (await c.get("/risk")).text
        page = (await c.get("/strategies")).text
        assert "view only" in page and "Start sma_cross" not in page and ">Pause<" not in page
    async with holding() as rig, rig.client() as c:  # token configured, not unlocked
        assert "Unlock actions" in (await c.get("/strategies")).text
        assert (await c.post("/api/actions/order", json={})).status_code == 401
    async with holding() as rig, rig.client(headers=AUTH) as c:  # live account
        rig.broker.paper = False
        for path in ("order", "order/preview", "limits", "strategies/add", "strategies/pause"):
            r = await c.post(f"/api/actions/{path}", json={"id": "sma_cross-1"})
            assert r.status_code == 403 and "live account" in r.json()["error"], path


async def test_full_session_without_the_cli():
    """Start a strategy, trade, tighten a limit, halt, flatten, stop: all from the console."""
    import asyncio

    rig = Rig(events=ticks("AAPL", [100.0] * 2000), token=TOKEN)
    rig.data._delay = 0.005
    rig.writer.start()
    rig.service.start(rig.writer)
    run = asyncio.create_task(rig.engine.run())
    await asyncio.sleep(0.05)
    async with rig.client(headers=AUTH) as c:
        post = lambda path, **body: c.post(f"/api/actions/{path}", json=body)  # noqa: E731
        assert (
            await post("strategies/add", strategy="rsi_reversion", symbols=["AAPL"], timeframe="1m")
        ).status_code == 200
        assert (await post("order", symbol="AAPL", side="buy", qty=4)).status_code == 200
        assert (await post("limits", max_position_qty=4)).status_code == 200
        assert (await post("order", symbol="AAPL", side="buy", qty=1)).status_code == 409  # over the new limit
        assert (await post("halt", reason="done for today")).status_code == 200
        assert (await post("flatten", confirm="FLATTEN")).status_code == 200
        assert rig.broker.positions == {}
        assert (await post("kill-switch", confirm="STOP")).status_code == 200
    await asyncio.wait_for(run, timeout=2)
    await rig.flush()
    assert [a["action"] for a in rig.store.audit_log()][::-1] == [
        "strategy_add",
        "order",
        "set_limits",
        "order",
        "halt",
        "flatten",
        "kill_switch",
    ]
    await rig.close()
