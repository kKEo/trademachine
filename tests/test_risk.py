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


def test_halted_also_blocks_position_reducing_orders():
    """Documented policy gap: a halt blocks exits too (operator guide, Known gaps)."""
    rm = RiskManager(RiskLimits())
    rm.halted, rm.halt_reason = True, "kill switch"
    ok, why = rm.check(req(Side.SELL, qty=1), tick(), [Position("AAPL", 1, 100)], None, now=T0)
    assert not ok and why == "halted: kill switch"


def test_limits_from_env(monkeypatch):
    monkeypatch.setenv("RISK_MAX_ORDER_NOTIONAL", "1234.5")
    monkeypatch.setenv("RISK_MAX_OPEN_POSITIONS", "2")
    monkeypatch.delenv("RISK_MAX_POSITION_QTY", raising=False)
    limits = RiskLimits.from_env()
    assert (limits.max_order_notional, limits.max_open_positions, limits.max_position_qty) == (1234.5, 2, 50)
