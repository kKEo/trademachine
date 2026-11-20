"""Operator controls: halt, resume, flatten, cancel, kill switch, orders in flight (Phase 4)."""

import asyncio
from datetime import timedelta

import pytest
from console_helpers import Rig, finished_run
from helpers import T0, ticks

from tradesignals.engine import OPEN_ORDER_FORGET_SEC, ActionRefused, SignalEngine
from tradesignals.events import EventBus, OutcomeKind, SignalOutcome, StatusEvent
from tradesignals.models import Order, OrderStatus, Side, Signal, SignalKind, Tick, utcnow
from tradesignals.providers.fake import PaperBroker, ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.strategies import SmaCross

HOLDING = [100.0] * 20 + [100.0 + i for i in range(1, 30)]  # ends long 3 AAPL
TOKEN = "s3cret-token"
AUTH = {"x-console-token": TOKEN}


class RestingBroker(PaperBroker):
    """A broker whose orders stay ACCEPTED until the test fills them: what a real market does."""

    name = "resting"

    def __init__(self):
        super().__init__()
        self.submitted: list = []
        self.cancelled: list[str] = []
        self.cancel_all_calls = 0

    async def submit_order(self, req):
        self.submitted.append(req)
        order = self._order(req, OrderStatus.ACCEPTED, 0.0, None)
        return order

    async def fill(self, order_id: str) -> None:
        o = self.orders[order_id]
        signed = o.qty if o.side == Side.BUY else -o.qty
        from tradesignals.models import Position

        cur = self.positions.get(o.symbol)
        qty = (cur.qty if cur else 0.0) + signed
        if abs(qty) < 1e-9:
            self.positions.pop(o.symbol, None)
        else:
            self.positions[o.symbol] = Position(o.symbol, qty, 100.0)
        o.status, o.filled_qty, o.avg_fill_price = OrderStatus.FILLED, o.qty, 100.0
        await self._updates.put(o)

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    async def cancel_all(self):
        self.cancel_all_calls += 1


def live_engine(broker=None, bus=None, limits=None, qty=3.0):
    """An engine that is not running a stream: tests drive it by hand."""
    broker = broker or PaperBroker()
    broker.mark("AAPL", 100.0)
    eng = SignalEngine(
        ReplayProvider([]),
        [SmaCross(["AAPL"])],
        broker=broker,
        risk=RiskManager(limits or RiskLimits(max_order_notional=10_000, max_quote_age_sec=10**9)),
        execute=True,
        default_qty=qty,
        warmup=False,
        bus=bus,
    )
    eng.last_tick["AAPL"] = Tick("AAPL", 99.99, 100.01, utcnow(), "test")
    return eng, broker


def sig(kind=SignalKind.LONG) -> Signal:
    return Signal("AAPL", kind, "sma_cross", T0)


# ---------------------------------------------------------------------- halt / resume / flatten
async def test_halt_blocks_entries_but_flat_signals_still_exit():
    bus = EventBus()
    outcomes = bus.subscribe(types=(SignalOutcome, StatusEvent))
    eng, broker = live_engine(bus=bus)
    await eng._emit(sig(SignalKind.LONG))
    assert broker.positions["AAPL"].qty == 3
    eng.halt("testing")
    assert eng.halted and eng.risk.halt_reason == "testing"
    await eng._emit(sig(SignalKind.FLAT))  # the strategy wants out: must pass although halted
    assert "AAPL" not in broker.positions
    await eng._emit(sig(SignalKind.LONG))  # ...but it cannot get back in
    assert "AAPL" not in broker.positions
    kinds = [e.kind for e in iter(outcomes.get_nowait, None) if isinstance(e, SignalOutcome)]
    assert kinds == [OutcomeKind.ORDER, OutcomeKind.ORDER, OutcomeKind.RISK_REJECT]
    eng.resume()
    await eng._emit(sig(SignalKind.LONG))
    assert broker.positions["AAPL"].qty == 3 and not eng.halted


async def test_flatten_after_a_halt_succeeds_and_publishes_orders():
    eng, broker = live_engine()
    broker.mark("MSFT", 50.0)
    await eng._emit(sig(SignalKind.LONG))
    eng.halt("loss")
    results = await eng.flatten()
    [(symbol, order, detail)] = results
    assert symbol == "AAPL" and order.side == Side.SELL and order.qty == 3 and order.status == OrderStatus.FILLED
    assert broker.positions == {} and detail == "sell 3"
    assert await eng.flatten() == []  # nothing left
    assert eng.halted  # flattening does not lift the halt


async def test_flatten_one_symbol_and_a_short():
    eng, broker = live_engine(limits=RiskLimits(max_order_notional=10**6, max_quote_age_sec=10**9))
    from tradesignals.models import Position

    broker.positions = {"AAPL": Position("AAPL", 3, 100.0), "MSFT": Position("MSFT", -2, 50.0)}
    broker.mark("MSFT", 50.0)
    [(symbol, order, _)] = await eng.flatten("MSFT")
    assert symbol == "MSFT" and order.side == Side.BUY and order.qty == 2
    assert set(broker.positions) == {"AAPL"}
    assert await eng.flatten("NOPE") == []


async def test_controls_need_a_broker():
    eng = SignalEngine(ReplayProvider([]), [SmaCross(["AAPL"])], warmup=False)
    with pytest.raises(ActionRefused):
        eng.halt()
    with pytest.raises(ActionRefused):
        eng.resume()
    with pytest.raises(ActionRefused):
        await eng.flatten()
    with pytest.raises(ActionRefused):
        await eng.cancel_order("x")
    assert eng.halted is False and eng.open_orders == []


# ---------------------------------------------------------------------- orders in flight (was gap H2)
async def test_second_signal_before_the_fill_does_not_duplicate_the_order():
    broker = RestingBroker()
    eng, _ = live_engine(broker)
    await eng._emit(sig(SignalKind.LONG))
    await eng._emit(sig(SignalKind.LONG))  # the first order has not filled yet
    assert len(broker.submitted) == 1 and [o.qty for o in eng.open_orders] == [3]
    # an exit while the entry is still in flight sizes against the position-to-be
    await eng._emit(sig(SignalKind.FLAT))
    assert [(r.side, r.qty) for r in broker.submitted] == [(Side.BUY, 3), (Side.SELL, 3)]


async def test_open_orders_are_forgotten_once_the_broker_reports_them_terminal():
    broker = RestingBroker()
    eng, _ = live_engine(broker)
    updates = asyncio.create_task(eng._consume_order_updates())
    await eng._emit(sig(SignalKind.LONG))
    [open_order] = eng.open_orders
    await broker.fill(open_order.id)
    await asyncio.sleep(0.01)
    assert eng.open_orders == [] and eng.positions[0].qty == 3
    await eng._emit(sig(SignalKind.LONG))  # now at target: nothing to do
    assert len(broker.submitted) == 1
    updates.cancel()


async def test_risk_judges_the_position_as_it_will_be_once_open_orders_fill():
    broker = RestingBroker()
    eng, _ = live_engine(
        broker, limits=RiskLimits(max_position_qty=3, max_order_notional=10**6, max_quote_age_sec=10**9)
    )
    await eng._emit(sig(SignalKind.LONG))  # 3 in flight, limit is 3
    eng.default_qty = 5
    await eng._emit(sig(SignalKind.LONG))  # wants 2 more: 3 pending + 2 > 3
    assert len(broker.submitted) == 1
    [p] = eng._positions_with_pending()
    assert (p.symbol, p.qty) == ("AAPL", 3)


async def test_flatten_twice_while_the_exit_is_in_flight_sends_one_order():
    """A double click must not sell twice."""
    broker = RestingBroker()
    from tradesignals.models import Position

    broker.positions = {"AAPL": Position("AAPL", 3, 100.0)}
    eng, _ = live_engine(broker)
    first, second = await asyncio.gather(eng.flatten(), eng.flatten())
    sent = [r for r in first + second if r[1] is not None]
    assert len(sent) == 1 and len(broker.submitted) == 1
    skipped = [r for r in first + second if r[1] is None]
    assert skipped[0][2] == "an order that closes it is already in flight"


async def test_stale_open_order_is_rechecked_and_eventually_forgotten():
    broker = RestingBroker()
    eng, _ = live_engine(broker)
    await eng._emit(sig(SignalKind.LONG))
    [o] = eng.open_orders
    # the push update was lost; the broker knows the order filled
    broker.orders[o.id].status = OrderStatus.FILLED
    eng._open_orders[o.id].ts = utcnow() - timedelta(seconds=60)
    await eng._reconcile_open_orders()
    assert eng.open_orders == []
    # an order the broker cannot find is dropped after the forget window
    ghost = Order(
        "ghost", "c", "AAPL", Side.BUY, 1, OrderStatus.ACCEPTED, ts=utcnow() - timedelta(seconds=61), source="resting"
    )
    eng._open_orders["ghost"] = ghost
    await eng._reconcile_open_orders()
    assert [x.id for x in eng.open_orders] == ["ghost"]  # too young to forget
    ghost.ts = utcnow() - timedelta(seconds=OPEN_ORDER_FORGET_SEC + 1)
    await eng._reconcile_open_orders()
    assert eng.open_orders == []


# ---------------------------------------------------------------------- kill switch
async def test_kill_switch_halts_cancels_and_stops_a_running_engine():
    broker = RestingBroker()
    bus = EventBus()
    statuses = bus.subscribe(types=(StatusEvent,))
    data = ReplayProvider(ticks("AAPL", [100.0] * 500), delay=0.01)
    eng = SignalEngine(data, [SmaCross(["AAPL"])], broker=broker, risk=RiskManager(RiskLimits()), warmup=False, bus=bus)
    run = asyncio.create_task(eng.run())
    await asyncio.sleep(0.05)
    assert eng.running
    await eng.kill_switch()
    await asyncio.wait_for(run, timeout=2)
    assert not eng.running and eng.halted and eng.risk.halt_reason == "kill switch" and broker.cancel_all_calls == 1
    seen = [(e.state, e.halted) for e in iter(statuses.get_nowait, None)]
    assert ("running", True) in seen and seen[-1] == ("stopped", True)


# ---------------------------------------------------------------------- console: access
async def test_token_matrix_for_every_action():
    actions = ["halt", "resume", "flatten", "cancel", "kill-switch"]
    async with finished_run(events=ticks("AAPL", HOLDING)) as rig, rig.client() as c:  # no token configured
        for a in actions:
            r = await c.post(f"/api/actions/{a}", json={})
            assert r.status_code == 403 and "read-only" in r.json()["error"]
        assert (await c.post("/unlock", json={"token": "x"})).status_code == 403
        assert rig.store.audit_log() == [] and not rig.engine.halted
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig, rig.client() as c:
        for a in actions:
            assert (await c.post(f"/api/actions/{a}", json={})).status_code == 401
            assert (await c.post(f"/api/actions/{a}", json={}, headers={"x-console-token": "wrong"})).status_code == 401
            r = await c.post(f"/api/actions/{a}", json={}, headers={**AUTH, "origin": "http://evil.example"})
            assert r.status_code == 403
        assert rig.store.audit_log() == [] and not rig.engine.halted and len(rig.engine.positions) == 1


async def test_unlock_sets_a_strict_http_only_cookie_and_lock_clears_it():
    async with finished_run(token=TOKEN) as rig, rig.client() as c:
        bad = await c.post("/unlock", json={"token": "nope"})
        assert bad.status_code == 401 and "set-cookie" not in bad.headers
        good = await c.post("/unlock", json={"token": TOKEN})
        cookie = good.headers["set-cookie"].lower()
        assert good.status_code == 200 and "httponly" in cookie and "samesite=strict" in cookie
        assert (await c.post("/api/actions/halt", json={})).status_code == 200  # the cookie now authorises
        assert "Lock actions" in (await c.get("/risk")).text
        await c.post("/lock")
        c.cookies.clear()
        assert (await c.post("/api/actions/resume", json={})).status_code == 401
        assert "Console token" in (await c.get("/risk")).text


# ---------------------------------------------------------------------- console: actions
async def test_halt_flatten_resume_from_the_console_with_audit_trail():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig, rig.client(headers=AUTH) as c:
        r = await c.post("/api/actions/halt", json={"reason": "news"})
        assert r.status_code == 200 and rig.engine.halted and rig.risk.halt_reason == "news"
        assert "Trading is halted" in (await c.get("/risk")).text and 'data-halted="true"' in (await c.get("/")).text

        r = await c.post("/api/actions/flatten", json={})  # all positions need the typed word
        assert r.status_code == 400 and "FLATTEN" in r.json()["error"] and len(rig.engine.positions) == 1
        r = await c.post("/api/actions/flatten", json={"confirm": "FLATTEN"})
        assert r.status_code == 200 and "sell 3 AAPL" in r.json()["message"]
        assert rig.broker.positions == {} and rig.engine.halted  # worked although halted
        assert (await c.post("/api/actions/flatten", json={"confirm": "FLATTEN"})).json()[
            "message"
        ] == "There are no open positions."

        assert (await c.post("/api/actions/resume", json={})).status_code == 200 and not rig.engine.halted
        assert (await c.post("/api/actions/resume", json={})).status_code == 409  # not halted any more

        await rig.flush()
        log = rig.store.audit_log()
        assert [a["action"] for a in log][::-1] == ["halt", "flatten", "flatten", "flatten", "resume", "resume"]
        assert (
            log[-1]["detail"] == "news"
            and log[-2]["result"].startswith("refused")
            and log[0]["result"].startswith("refused")
        )
        flat_order = rig.store.orders(limit=1)[0]
        assert flat_order["origin"] == "flatten" and flat_order["signal_id"] is None
        assert "flatten" in (await c.get("/partials/audit")).text


async def test_flatten_one_position_from_the_console():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig, rig.client(headers=AUTH) as c:
        assert 'data-action="/api/actions/flatten"' in (await c.get("/portfolio")).text
        r = await c.post("/api/actions/flatten", json={"symbol": "msft"})
        assert r.status_code == 409 and "no open position in MSFT" in r.json()["error"]
        r = await c.post("/api/actions/flatten", json={"symbol": "aapl"})
        assert r.status_code == 200 and rig.broker.positions == {}


async def test_console_kill_switch_needs_the_typed_word_and_stops_the_engine():
    rig = Rig(events=ticks("AAPL", [100.0] * 500), token=TOKEN)
    rig.data._delay = 0.01
    rig.writer.start()
    rig.service.start(rig.writer)
    run = asyncio.create_task(rig.engine.run())
    await asyncio.sleep(0.05)
    async with rig.client(headers=AUTH) as c:
        assert (await c.post("/api/actions/kill-switch", json={"confirm": "stop"})).status_code == 400
        assert rig.engine.running
        r = await c.post("/api/actions/kill-switch", json={"confirm": "STOP"})
        assert r.status_code == 200 and "engine stopping" in r.json()["message"]
    await asyncio.wait_for(run, timeout=2)
    assert not rig.engine.running and rig.engine.halted
    await rig.flush()
    assert [a["action"] for a in rig.store.audit_log()] == ["kill_switch", "kill_switch"]
    await rig.close()


async def test_cancel_an_open_order_from_the_console():
    rig = Rig(token=TOKEN)
    rig.engine.broker = broker = RestingBroker()
    broker.mark("AAPL", 100.0)
    rig.engine.last_tick["AAPL"] = Tick("AAPL", 99.99, 100.01, utcnow(), "test")
    await rig.engine._emit(sig(SignalKind.LONG))
    [o] = rig.engine.open_orders
    async with rig.client(headers=AUTH) as c:
        html = (await c.get("/portfolio")).text
        assert "Cancel order" in html and o.id in html
        assert (await c.get("/api/risk")).json()["open_orders"][0]["id"] == o.id
        assert (await c.post("/api/actions/cancel", json={"order_id": "nope"})).status_code == 409
        assert (await c.post("/api/actions/cancel", json={"order_id": o.id})).status_code == 200
    assert broker.cancelled == [o.id]


async def test_live_account_allows_halt_and_kill_switch_only():
    """In this phase the console trades paper and demo accounts only."""
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig, rig.client(headers=AUTH) as c:
        rig.broker.paper = False
        for action, body in (
            ("flatten", {"confirm": "FLATTEN"}),
            ("flatten", {"symbol": "AAPL"}),
            ("cancel", {"order_id": "x"}),
        ):
            r = await c.post(f"/api/actions/{action}", json=body)
            assert r.status_code == 403 and "live account" in r.json()["error"]
        assert len(rig.engine.positions) == 1
        assert (await c.post("/api/actions/halt", json={})).status_code == 200 and rig.engine.halted
        assert (await c.post("/api/actions/resume", json={})).status_code == 403  # resuming trades a live account
        page = (await c.get("/risk")).text
        assert "live account" in page and "Halting and the kill switch still work" in page
        assert 'data-mode="live"' in page
        assert "Close</summary>" not in (await c.get("/portfolio")).text  # no trading buttons offered


async def test_engine_without_execute_refuses_console_trading():
    async with (
        finished_run(events=ticks("AAPL", HOLDING), execute=False, token=TOKEN) as rig,
        rig.client(headers=AUTH) as c,
    ):
        r = await c.post("/api/actions/flatten", json={"confirm": "FLATTEN"})
        assert r.status_code == 403 and "--execute" in r.json()["error"]


async def test_action_failure_is_reported_and_audited():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig, rig.client(headers=AUTH) as c:

        async def boom(symbol=None):
            raise RuntimeError("broker unreachable")

        rig.engine.flatten = boom
        r = await c.post("/api/actions/flatten", json={"confirm": "FLATTEN"})
        assert r.status_code == 502 and "broker unreachable" in r.json()["error"]
        assert rig.store.audit_log()[0]["result"] == "failed: broker unreachable"


# ---------------------------------------------------------------------- console: pages
async def test_risk_page_shows_limits_usage_and_view_only_note():
    async with finished_run(events=ticks("AAPL", HOLDING)) as rig, rig.client() as c:
        page = (await c.get("/risk")).text
        assert "Loss since the session started" in page and "Open positions" in page and "view only" in page
        assert "Stop the engine" not in page and "View only" in page
        view = (await c.get("/api/risk")).json()
        rows = {r["name"]: r for r in view["risk"]["rows"]}
        assert rows["Open positions"]["used"] == 1 and rows["Open positions"]["share_pct"] == 20
        assert rows["Largest position, in shares"]["used"] == 3 and view["capabilities"]["can_trade"] is True
        assert (await c.get("/partials/risk")).status_code == 200
        assert (await c.get("/api/audit")).json() == []
    async with finished_run(broker=False, execute=False) as rig, rig.client() as c:
        assert "no risk limits apply" in (await c.get("/risk")).text
        assert (await c.get("/api/risk")).json()["risk"] is None


async def test_unlocked_risk_page_offers_the_controls():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig, rig.client() as c:
        assert "Unlock actions with the console token" in (await c.get("/risk")).text
        await c.post("/unlock", json={"token": TOKEN})
        page = (await c.get("/risk")).text
        for text in ("Halt trading", "Close every position", "Stop the engine", "Type FLATTEN", "Type STOP"):
            assert text in page
        assert (await c.get("/partials/access")).status_code == 200
        assert (await c.get("/partials/open-orders")).text.count("No orders are waiting") == 1
