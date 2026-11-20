"""Event bus, SQLite store and the engine's publishing (Phase 0 of the admin console)."""

import asyncio

from helpers import T0, bars, ticks, wave

from tradesignals.engine import MAX_HISTORY, SignalEngine
from tradesignals.events import (
    BarEvent,
    EventBus,
    OrderEvent,
    OutcomeKind,
    QuoteEvent,
    SignalEvent,
    SignalOutcome,
    SnapshotEvent,
    StatusEvent,
)
from tradesignals.models import Account, Order, OrderStatus, Position, Side, Signal, SignalKind
from tradesignals.providers.fake import PaperBroker, ReplayProvider
from tradesignals.risk import RiskLimits, RiskManager
from tradesignals.store import Store, StoreWriter, to_epoch
from tradesignals.strategies import SmaCross


def drain(sub) -> list:
    out = []
    while (ev := sub.get_nowait()) is not None:
        out.append(ev)
    return out


def paper_engine(bus, prices=None, limits=None, **kw):
    broker = PaperBroker()
    data = ReplayProvider(ticks("AAPL", prices or wave(120)))
    risk = RiskManager(limits or RiskLimits(max_order_notional=10_000, max_quote_age_sec=10**9))
    eng = SignalEngine(
        data,
        [SmaCross(["AAPL"], fast=5, slow=15)],
        broker=broker,
        risk=risk,
        execute=True,
        default_qty=3,
        warmup=False,
        on_tick=lambda t: broker.mark(t.symbol, t.price),
        bus=bus,
        **kw,
    )
    return eng, broker


# ---------------------------------------------------------------------- bus
async def test_bus_fans_out_to_every_subscriber_and_filters_by_type():
    bus = EventBus()
    everything = bus.subscribe(name="all")
    only_status = bus.subscribe(types=(StatusEvent,), name="status")
    bus.publish(StatusEvent("running"))
    bus.publish(BarEvent(bars("AAPL", [1.0])[0]))
    assert [type(e) for e in drain(everything)] == [StatusEvent, BarEvent]
    assert [type(e) for e in drain(only_status)] == [StatusEvent]


async def test_bus_drops_oldest_for_a_slow_subscriber_and_never_blocks():
    bus = EventBus()
    slow = bus.subscribe(maxsize=3, name="slow")
    for i in range(10):
        bus.publish(StatusEvent("running", halt_reason=str(i)))
    assert slow.dropped == 7
    assert [e.halt_reason for e in drain(slow)] == ["7", "8", "9"]


async def test_bus_closed_subscription_receives_nothing():
    bus = EventBus()
    sub = bus.subscribe()
    sub.close()
    bus.publish(StatusEvent("running"))
    assert drain(sub) == []


# ---------------------------------------------------------------------- store
def _order(status=OrderStatus.FILLED, filled=3.0, price=101.0, oid="o1") -> Order:
    return Order(oid, "c1", "AAPL", Side.BUY, 3.0, status, filled, price, ts=T0, source="paper")


def test_store_round_trips_signals_outcomes_orders_bars_and_equity():
    store = Store()
    run = store.start_run(data="replay", broker="paper", paper=True, execute=True, symbols=["AAPL"])
    sig = Signal("AAPL", SignalKind.LONG, "sma_cross", T0, reason="fast>slow")
    store.apply(
        run,
        [
            SignalEvent("s1", sig),
            SignalOutcome("s1", OutcomeKind.ORDER, "buy 3", "c1", ts=T0),
            OrderEvent(_order(), origin="sma_cross:long", signal_id="s1", ts=T0),
            BarEvent(bars("AAPL", [100.0, 101.0])[0]),
            BarEvent(bars("AAPL", [100.0, 101.0])[1]),
            SnapshotEvent(Account(100_010.0, 99_697.0, 99_697.0), (Position("AAPL", 3, 101.0),), ts=T0),
            StatusEvent("running"),  # not persisted, must be ignored
        ],
    )
    [row] = store.signals()
    assert (row["id"], row["kind"], row["outcome"], row["client_order_id"]) == ("s1", "long", "order", "c1")
    assert store.order_by_client_id("c1")["origin"] == "sma_cross:long"
    assert [b["close"] for b in store.bars("AAPL", "1m")] == [100.0, 101.0]
    assert store.equity()[0]["equity"] == 100_010.0
    assert store.last_snapshot()["positions"][0]["symbol"] == "AAPL"
    assert store.runs()[0]["symbols"] == "AAPL"
    store.end_run(run)
    assert store.runs()[0]["stopped_at"] is not None


def test_store_filters_signals_and_limits_bars_to_the_newest():
    store = Store()
    run = store.start_run()
    evs = [
        SignalEvent(f"s{i}", Signal(sym, kind, "sma_cross", T0.replace(minute=i)))
        for i, (sym, kind) in enumerate(
            [("AAPL", SignalKind.LONG), ("MSFT", SignalKind.FLAT), ("AAPL", SignalKind.FLAT)]
        )
    ]
    store.apply(run, evs + [BarEvent(b) for b in bars("AAPL", [1, 2, 3, 4, 5])])
    assert [r["id"] for r in store.signals()] == ["s2", "s1", "s0"]  # newest first
    assert [r["id"] for r in store.signals(symbol="AAPL", kind="flat")] == ["s2"]
    assert [r["id"] for r in store.signals(since=to_epoch(T0.replace(minute=1)))] == ["s2", "s1"]
    assert [b["close"] for b in store.bars("AAPL", "1m", limit=2)] == [4, 5]


def test_store_bars_are_deduplicated_by_start():
    store = Store()
    [b] = bars("AAPL", [1.0])
    store.put_bars([b, b, b])
    assert store.counts()["bars"] == 1


def test_store_push_update_before_submit_reply_keeps_terminal_state_and_origin():
    """A broker's push update can overtake the reply to our submit; neither view may be lost."""
    store = Store()
    run = store.start_run()
    store.apply(run, [OrderEvent(_order(OrderStatus.FILLED), update=True, ts=T0)])
    assert store.orders()[0]["origin"] == "external"
    store.apply(run, [OrderEvent(_order(OrderStatus.ACCEPTED, 0.0, None), origin="sma_cross:long", signal_id="s1")])
    [row] = store.orders()
    assert (row["status"], row["filled_qty"], row["origin"], row["signal_id"]) == (
        "filled",
        3.0,
        "sma_cross:long",
        "s1",
    )
    assert len(store.order_updates("paper", "o1")) == 2


def test_store_audit_log():
    store = Store()
    store.audit("kill_switch", "from console", "ok")
    assert store.audit_log()[0]["action"] == "kill_switch"


def test_store_creates_the_file_and_its_directory(tmp_path):
    path = tmp_path / "nested" / "ts.db"
    Store(path).close()
    assert path.exists()


# ---------------------------------------------------------------------- engine publishing
async def test_engine_publishes_signal_outcome_order_bar_snapshot_and_status():
    bus = EventBus()
    sub = bus.subscribe(maxsize=10_000)
    eng, _ = paper_engine(bus)
    await eng.run()
    evs = drain(sub)
    signals = [e for e in evs if isinstance(e, SignalEvent)]
    outcomes = [e for e in evs if isinstance(e, SignalOutcome)]
    orders = [e for e in evs if isinstance(e, OrderEvent) and not e.update]
    assert len(signals) == len(eng.signals) >= 3
    assert len(outcomes) == len(signals)  # every signal in execute mode gets exactly one outcome
    assert outcomes[0].kind is OutcomeKind.NO_OP  # the wave's first signal is FLAT with no position
    assert {o.kind for o in outcomes[1:]} == {OutcomeKind.ORDER}
    # each order is linked to the signal that caused it, through the signal id and the client order id
    by_signal = {o.signal_id: o for o in outcomes}
    for ev in orders:
        assert by_signal[ev.signal_id].client_order_id == ev.order.client_order_id
        assert ev.origin.startswith("sma_cross:")
    assert sum(isinstance(e, BarEvent) for e in evs) == 119  # 120 one-minute ticks close 119 bars
    assert any(isinstance(e, OrderEvent) and e.update for e in evs)
    statuses = [e.state for e in evs if isinstance(e, StatusEvent)]
    assert statuses[0] == "running" and statuses[-1] == "stopped"
    snaps = [e for e in evs if isinstance(e, SnapshotEvent)]
    assert snaps and snaps[-1].account is not None


async def test_engine_publishes_risk_reject_outcome():
    bus = EventBus()
    sub = bus.subscribe(maxsize=10_000, types=(SignalOutcome,))
    eng, _ = paper_engine(bus, limits=RiskLimits(max_order_notional=50, max_quote_age_sec=10**9))
    await eng.run()
    kinds = {e.kind for e in drain(sub)}
    assert OutcomeKind.RISK_REJECT in kinds and OutcomeKind.ORDER not in kinds


async def test_engine_publishes_submit_failure_outcome():
    bus = EventBus()
    sub = bus.subscribe(maxsize=10_000, types=(SignalOutcome,))
    eng, broker = paper_engine(bus)

    async def boom(req):
        raise RuntimeError("broker down")

    broker.submit_order = boom
    await eng.run()
    failed = [e for e in drain(sub) if e.kind is OutcomeKind.SUBMIT_FAILED]
    assert failed and "broker down" in failed[0].detail


async def test_engine_throttles_quote_events_per_symbol():
    bus = EventBus()
    sub = bus.subscribe(maxsize=10_000, types=(QuoteEvent,))
    eng, _ = paper_engine(bus, quote_interval=3600)
    await eng.run()
    assert len(drain(sub)) == 1  # 120 ticks within one interval -> one quote event


async def test_engine_heartbeat_publishes_snapshots_while_running():
    bus = EventBus()
    sub = bus.subscribe(maxsize=10_000, types=(SnapshotEvent,))
    broker = PaperBroker()
    data = ReplayProvider(ticks("AAPL", [100.0] * 5), delay=0.02)
    eng = SignalEngine(data, [SmaCross(["AAPL"])], broker=broker, warmup=False, bus=bus, snapshot_interval=0.01)
    await eng.run()
    assert len(drain(sub)) >= 4  # start + several heartbeats + final


async def test_engine_without_bus_is_unchanged_and_history_is_bounded():
    data = ReplayProvider(ticks("AAPL", wave(120)))
    eng = SignalEngine(data, [SmaCross(["AAPL"], fast=5, slow=15)], warmup=False)
    await eng.run()
    assert eng.signals and eng.bus is None
    sig = eng.signals[0]
    for _ in range(MAX_HISTORY + 50):
        await eng._emit(sig)
    assert len(eng.signals) == MAX_HISTORY


# ---------------------------------------------------------------------- writer
async def test_replay_run_leaves_a_complete_database(tmp_path):
    bus = EventBus()
    store = Store(tmp_path / "ts.db")
    eng, broker = paper_engine(bus)
    run = store.start_run(data="replay", broker="paper", paper=True, execute=True, symbols=["AAPL"])
    writer = StoreWriter(store, bus, run)
    writer.start()
    await eng.run()
    await writer.stop()
    store.end_run(run)

    counts = store.counts()
    assert counts["signals"] == len(eng.signals)
    assert counts["signal_outcomes"] == len(eng.signals)
    assert counts["orders"] == len(eng.orders) >= 2
    assert counts["bars"] == 119
    assert counts["equity_snapshots"] >= 2
    assert all(o["status"] == "filled" and o["signal_id"] for o in store.orders())
    executed = [s for s in store.signals() if s["outcome"] == "order"]
    assert all(store.order_by_client_id(s["client_order_id"]) for s in executed)
    account = await broker.get_account()
    assert store.equity()[-1]["equity"] == account.equity
    store.close()

    reopened = Store(tmp_path / "ts.db")  # history survives a restart
    assert reopened.counts()["signals"] == counts["signals"]


async def test_store_failure_never_stops_trading():
    class BrokenStore(Store):
        def apply(self, run_id, events):
            raise RuntimeError("disk full")

    bus = EventBus()
    eng, _ = paper_engine(bus)
    writer = StoreWriter(BrokenStore(), bus, "run")
    writer.start()
    await eng.run()
    await asyncio.sleep(0)
    await writer.stop()
    assert len(eng.orders) >= 2  # trading went on
    assert writer.failed_batches >= 1
