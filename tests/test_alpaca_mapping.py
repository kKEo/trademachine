"""Offline tests of the Alpaca adapter's pure mapping helpers (alpaca-py is optional)."""

from enum import Enum

import pytest

from tradesignals.models import OrderStatus, Side, Timeframe
from tradesignals.providers.alpaca import _TF, _creds, _enum_value, _to_order


class FakeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class FakeStatus(str, Enum):
    FILLED = "filled"
    PENDING_NEW = "pending_new"
    HELD = "held"


class FakeOrder:
    def __init__(self, side, status, qty="3", filled_qty="0", filled_avg_price=None):
        self.id, self.client_order_id, self.symbol = "o-1", "c-1", "AAPL"
        self.side, self.status = side, status
        self.qty, self.filled_qty, self.filled_avg_price = qty, filled_qty, filled_avg_price


def test_str_of_a_str_enum_is_not_its_value():
    assert str(FakeSide.BUY) == "FakeSide.BUY"  # the trap the old mapping fell into
    assert _enum_value(FakeSide.BUY) == "buy"
    assert _enum_value("Buy") == "buy"


@pytest.mark.parametrize(
    "side,expected",
    [
        (FakeSide.BUY, Side.BUY),
        (FakeSide.SELL, Side.SELL),
        ("buy", Side.BUY),
        ("sell", Side.SELL),
    ],
)
def test_to_order_maps_side_from_enum_or_string(side, expected):
    assert _to_order(FakeOrder(side, FakeStatus.FILLED)).side == expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (FakeStatus.FILLED, OrderStatus.FILLED),
        (FakeStatus.PENDING_NEW, OrderStatus.NEW),
        (FakeStatus.HELD, OrderStatus.UNKNOWN),
        ("canceled", OrderStatus.CANCELLED),
    ],
)
def test_to_order_maps_status(status, expected):
    assert _to_order(FakeOrder(FakeSide.BUY, status)).status == expected


def test_to_order_numeric_fields():
    o = _to_order(FakeOrder(FakeSide.BUY, FakeStatus.FILLED, qty="3", filled_qty="3", filled_avg_price="101.5"))
    assert (o.qty, o.filled_qty, o.avg_fill_price, o.source, o.id) == (3.0, 3.0, 101.5, "alpaca", "o-1")
    assert _to_order(FakeOrder(FakeSide.BUY, FakeStatus.FILLED, qty=None)).qty == 0.0


def test_timeframe_units_are_enum_attribute_names():
    assert {unit for unit, _ in _TF.values()} <= {"Minute", "Hour", "Day"}
    assert _TF[Timeframe.M5] == ("Minute", 5)


def test_timeframe_lookup_against_real_alpaca_enum():
    tf_mod = pytest.importorskip("alpaca.data.timeframe")
    for unit, n in _TF.values():
        tf_mod.TimeFrame(n, tf_mod.TimeFrameUnit[unit])  # by name; TimeFrameUnit("Minute") would raise
    assert tf_mod.TimeFrameUnit["Minute"].value == "Min"


def test_creds_paper_flag_is_strict(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    assert _creds() == ("k", "s", True)
    monkeypatch.setenv("ALPACA_PAPER", "1")
    assert _creds()[2] is True
    monkeypatch.setenv("ALPACA_PAPER", "false")
    assert _creds()[2] is False
    monkeypatch.setenv("ALPACA_PAPER", "nope")
    with pytest.raises(ValueError):
        _creds()


def test_creds_require_both_keys(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    with pytest.raises(RuntimeError):
        _creds()
