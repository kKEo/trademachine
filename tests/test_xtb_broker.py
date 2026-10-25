"""Offline tests of the XTB adapter against a fake session that records every xAPI command."""

import logging
from datetime import timedelta

import pytest
from helpers import T0

from tradesignals.interfaces import SymbolMapper
from tradesignals.models import OrderRequest, OrderStatus, OrderType, Side, Timeframe
from tradesignals.providers.xtb import XtbBroker, XtbData, _ms_to_dt


class FakeSession:
    demo = True

    def __init__(self, trades=None, status=3, symbol=None, chart=None):
        self.calls: list[tuple[str, dict]] = []
        self.trades = trades or []
        self.status = status  # requestStatus returned by tradeTransactionStatus (3 = ACCEPTED)
        self.symbol = symbol or {"bid": 99.0, "ask": 101.0}
        self.chart = chart
        self.next_order = 1000

    async def connect(self): ...

    async def close(self): ...

    async def command(self, cmd, **args):
        self.calls.append((cmd, args))
        if cmd == "getSymbol":
            return self.symbol
        if cmd == "getTrades":
            return self.trades
        if cmd == "tradeTransaction":
            self.next_order += 1
            return {"order": self.next_order}
        if cmd == "tradeTransactionStatus":
            return {
                "order": args["order"],
                "requestStatus": self.status,
                "message": "",
                "ask": 101.0,
                "bid": 99.0,
                "customComment": "",
            }
        if cmd == "getChartLastRequest":
            return self.chart
        if cmd == "getMarginLevel":
            return {"equity": 1000.0, "balance": 900.0, "margin_free": 800.0, "currency": "EUR"}
        raise AssertionError(f"unexpected command {cmd}")

    def transactions(self):
        return [a["tradeTransInfo"] for c, a in self.calls if c == "tradeTransaction"]


def trade(order, cmd, symbol="AAPL.US_9", volume=1.0, open_price=90.0, closed=False, profit=0.0):
    return {
        "symbol": symbol,
        "cmd": cmd,
        "volume": volume,
        "position": order,
        "order": order,
        "open_price": open_price,
        "profit": profit,
        "closed": closed,
    }


def broker(session) -> XtbBroker:
    return XtbBroker(session=session, mapper=SymbolMapper(".US_9"))


def test_paper_flag_follows_session_demo():
    assert broker(FakeSession()).paper is True
    s = FakeSession()
    s.demo = False
    assert broker(s).paper is False


async def test_market_buy_opens_and_reports_fill_at_sent_price():
    s = FakeSession()
    b = broker(s)
    o = await b.submit_order(OrderRequest("AAPL", Side.BUY, 2))
    (info,) = s.transactions()
    assert (info["symbol"], info["cmd"], info["type"], info["price"], info["volume"]) == ("AAPL.US_9", 0, 0, 101.0, 2)
    assert (o.status, o.filled_qty, o.avg_fill_price, o.id) == (OrderStatus.FILLED, 2, 101.0, "1001")
    assert o.id not in b._pending


async def test_sell_closes_existing_long_position_with_type_2():
    s = FakeSession(trades=[trade(777, cmd=0, volume=5.0, open_price=95.0)])
    o = await broker(s).submit_order(OrderRequest("AAPL", Side.SELL, 2))
    (info,) = s.transactions()
    assert (info["type"], info["order"], info["volume"], info["price"], info["cmd"]) == (2, 777, 2, 99.0, 0)
    assert (o.status, o.avg_fill_price) == (OrderStatus.FILLED, 99.0)


async def test_sell_without_position_opens_a_fresh_sell():
    s = FakeSession()
    await broker(s).submit_order(OrderRequest("AAPL", Side.SELL, 1))
    (info,) = s.transactions()
    assert (info["cmd"], info["type"], info["price"]) == (1, 0, 99.0)  # a CFD short; rejected by XTB for real stocks


async def test_limit_order_accepted_stays_pending_not_filled():
    s = FakeSession()
    b = broker(s)
    o = await b.submit_order(OrderRequest("AAPL", Side.BUY, 1, type=OrderType.LIMIT, limit_price=90.0))
    (info,) = s.transactions()
    assert (info["cmd"], info["type"], info["price"]) == (2, 0, 90.0)
    assert (o.status, o.filled_qty, o.avg_fill_price) == (OrderStatus.ACCEPTED, 0.0, None)
    assert o.id in b._pending


async def test_rejected_transaction_is_reported_rejected():
    s = FakeSession(status=4)
    b = broker(s)
    o = await b.submit_order(OrderRequest("AAPL", Side.BUY, 1))
    assert o.status == OrderStatus.REJECTED and o.id not in b._pending


async def test_cancel_order_sends_delete_type_4_with_the_orders_own_fields():
    s = FakeSession(trades=[trade(55, cmd=2, volume=1.0, open_price=90.0)])
    await broker(s).cancel_order("55")
    (info,) = s.transactions()
    assert (info["type"], info["order"], info["cmd"], info["symbol"], info["volume"], info["price"]) == (
        4,
        55,
        2,
        "AAPL.US_9",
        1.0,
        90.0,
    )
    assert ("tradeTransactionStatus", {"order": 1001}) in s.calls


async def test_cancel_order_of_executed_or_unknown_order_raises():
    s = FakeSession(trades=[trade(1, cmd=0)])  # cmd 0 = executed market trade, not pending
    with pytest.raises(RuntimeError):
        await broker(s).cancel_order("1")
    with pytest.raises(RuntimeError):
        await broker(s).cancel_order("404")
    assert s.transactions() == []


async def test_cancel_all_deletes_every_pending_order_but_leaves_positions():
    s = FakeSession(trades=[trade(10, cmd=2), trade(11, cmd=4), trade(12, cmd=0), trade(13, cmd=3, closed=True)])
    await broker(s).cancel_all()
    assert [t["order"] for t in s.transactions()] == [10, 11]
    assert all(t["type"] == 4 for t in s.transactions())


async def test_cancel_all_rejection_is_logged_not_raised(caplog):
    s = FakeSession(trades=[trade(10, cmd=2)], status=4)
    with caplog.at_level(logging.WARNING, logger="tradesignals.xtb"):
        await broker(s).cancel_all()
    assert any("cancel 10 failed" in r.getMessage() for r in caplog.records)


async def test_get_positions_aggregates_trades_per_symbol():
    trades = [
        trade(1, cmd=0, volume=2.0, open_price=100.0, profit=5.0),
        trade(2, cmd=0, volume=2.0, open_price=110.0, profit=-1.0),
        trade(3, cmd=1, symbol="MSFT.US_9", volume=1.0, open_price=50.0),
        trade(4, cmd=2, symbol="MSFT.US_9"),
    ]  # pending order, not a position
    ps = {p.symbol: p for p in await broker(FakeSession(trades=trades)).get_positions()}
    assert (ps["AAPL"].qty, ps["AAPL"].avg_price, ps["AAPL"].unrealized_pnl, ps["AAPL"].provider_ref) == (
        4,
        105,
        4,
        "1",
    )
    assert ps["MSFT"].qty == -1
    assert ps["AAPL"].market_value is None  # not provided by xAPI trade records


async def test_get_account_maps_margin_level():
    a = await broker(FakeSession()).get_account()
    assert (a.equity, a.cash, a.buying_power, a.currency) == (1000.0, 900.0, 800.0, "EUR")


async def test_get_bars_decodes_digits_and_deltas():
    t1 = T0 + timedelta(minutes=1)
    chart = {
        "digits": 2,
        "rateInfos": [
            {"ctm": int(T0.timestamp() * 1000), "open": 10000, "high": 150, "low": -50, "close": 100, "vol": 12.0},
            {"ctm": int(t1.timestamp() * 1000), "open": 10100, "high": 0, "low": 0, "close": 0, "vol": 0},
        ],
    }
    s = FakeSession(chart=chart)
    out = await XtbData(session=s, mapper=SymbolMapper(".US_9")).get_bars("AAPL", Timeframe.M1, T0, end=T0)
    assert len(out) == 1
    b = out[0]
    assert (b.open, b.high, b.low, b.close, b.volume, b.start, b.source) == (100.0, 101.5, 99.5, 101.0, 12.0, T0, "xtb")
    cmd, args = s.calls[-1]
    assert cmd == "getChartLastRequest"
    assert args["info"] == {"period": 1, "start": int(T0.timestamp() * 1000), "symbol": "AAPL.US_9"}


def test_ms_to_dt_is_utc_aware():
    assert _ms_to_dt(int(T0.timestamp() * 1000)) == T0
