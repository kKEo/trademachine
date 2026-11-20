from datetime import timedelta

from helpers import T0

from tradesignals.models import Account, OrderRequest, Position, Side, Tick
from tradesignals.risk import RiskLimits, RiskManager


def tick(price: float = 100.0, ts=T0) -> Tick:
    return Tick("AAPL", price - 0.01, price + 0.01, ts, "test")


def req(side: Side = Side.BUY, qty: float = 1.0, **kw) -> OrderRequest:
    return OrderRequest("AAPL", side, qty, **kw)


def test_rejects_without_quote():
    ok, why = RiskManager(RiskLimits()).check(req(), None, [], None, now=T0)
    assert not ok and why == "no quote"


def test_rejects_stale_quote():
    rm = RiskManager(RiskLimits(max_quote_age_sec=15))
    assert rm.check(req(), tick(), [], None, now=T0 + timedelta(seconds=15))[0]
    ok, why = rm.check(req(), tick(), [], None, now=T0 + timedelta(seconds=16))
    assert not ok and why.startswith("stale quote")


def test_rejects_oversized_notional_using_limit_price_when_given():
    rm = RiskManager(RiskLimits(max_order_notional=500))
    assert rm.check(req(qty=5), tick(100), [], None, now=T0)[0]  # exactly 500 is allowed
    ok, why = rm.check(req(qty=5, limit_price=101), tick(100), [], None, now=T0)
    assert not ok and why.startswith("notional")


def test_rejects_position_limit_on_both_sides():
    rm = RiskManager(RiskLimits(max_position_qty=10))
    pos = [Position("AAPL", 8, 100)]
    assert rm.check(req(qty=2), tick(), pos, None, now=T0)[0]
    assert not rm.check(req(qty=3), tick(), pos, None, now=T0)[0]
    ok, why = rm.check(req(Side.SELL, qty=19), tick(), pos, None, now=T0)
    assert not ok and why.startswith("position would be")


def test_rejects_new_symbol_when_open_position_cap_reached():
    rm = RiskManager(RiskLimits(max_open_positions=1))
    ok, why = rm.check(req(), tick(), [Position("MSFT", 1, 10)], None, now=T0)
    assert not ok and why == "max open positions (1)"
    assert rm.check(req(), tick(), [Position("AAPL", 1, 10)], None, now=T0)[0]  # existing symbol is fine


def test_daily_loss_halts_and_latches_until_restart():
    rm = RiskManager(RiskLimits(max_daily_loss=200))
    assert rm.check(req(), tick(), [], Account(10_000, 10_000, 10_000), now=T0)[0]
    ok, why = rm.check(req(), tick(), [], Account(9_700, 9_700, 9_700), now=T0)
    assert not ok and why.startswith("daily loss") and rm.halted
    ok, why = rm.check(req(), tick(), [], Account(10_000, 10_000, 10_000), now=T0)  # equity recovered
    assert not ok and why.startswith("halted")  # ...but the halt is a latch


def test_daily_loss_check_is_skipped_when_account_unknown():
    rm = RiskManager(RiskLimits(max_daily_loss=0))
    assert rm.check(req(), tick(), [], None, now=T0)[0]


def test_halt_blocks_new_risk_but_never_an_exit():
    """A halt, a cap or a stale quote must not trap you in a position (was known gap H1)."""
    rm = RiskManager(RiskLimits(max_order_notional=1))
    rm.halt("kill switch")
    held = [Position("AAPL", 5, 100)]
    ok, why = rm.check(req(Side.BUY, qty=1), tick(), held, None, now=T0)
    assert not ok and why == "halted: kill switch"
    # selling part or all of the position passes: halted, over the notional cap, without any quote
    assert rm.check(req(Side.SELL, qty=1), None, held, None, now=T0) == (True, "ok: reduces position")
    assert rm.check(req(Side.SELL, qty=5), tick(), held, None, now=T0 + timedelta(hours=1))[0]
    # ...but selling through zero would open a short, which is new risk
    ok, why = rm.check(req(Side.SELL, qty=6), tick(), held, None, now=T0)
    assert not ok and why == "halted: kill switch"
    # covering a short is an exit too
    assert rm.check(req(Side.BUY, qty=2), None, [Position("AAPL", -2, 100)], None, now=T0)[0]
    # an order in a symbol we do not hold is never an exit
    assert not rm.check(req(Side.SELL, qty=1), tick(), [Position("MSFT", 5, 100)], None, now=T0)[0]


def test_resume_clears_the_halt_and_restarts_the_loss_baseline():
    rm = RiskManager(RiskLimits(max_daily_loss=200))
    assert rm.check(req(), tick(), [], Account(10_000, 10_000, 10_000), now=T0)[0]
    assert not rm.check(req(), tick(), [], Account(9_700, 9_700, 9_700), now=T0)[0] and rm.halted
    rm.resume()
    assert not rm.halted and rm.halt_reason == ""
    # carrying on from 9,700: the old loss does not halt again, a further 200 does
    assert rm.check(req(), tick(), [], Account(9_700, 9_700, 9_700), now=T0)[0]
    assert not rm.check(req(), tick(), [], Account(9_499, 9_499, 9_499), now=T0)[0]


def test_limits_from_env(monkeypatch):
    monkeypatch.setenv("RISK_MAX_ORDER_NOTIONAL", "1234.5")
    monkeypatch.setenv("RISK_MAX_OPEN_POSITIONS", "2")
    monkeypatch.delenv("RISK_MAX_POSITION_QTY", raising=False)
    limits = RiskLimits.from_env()
    assert (limits.max_order_notional, limits.max_open_positions, limits.max_position_qty) == (1234.5, 2, 50)
