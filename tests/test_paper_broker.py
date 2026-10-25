from datetime import timedelta

import pytest
from helpers import T0, bars

from tradesignals.models import OrderRequest, OrderStatus, OrderType, Side, Timeframe
from tradesignals.providers.fake import PaperBroker, ReplayProvider


async def test_adding_to_a_position_averages_the_cost_basis():
    b = PaperBroker()
    b.mark("AAPL", 100)
    await b.submit_order(OrderRequest("AAPL", Side.BUY, 10))
    b.mark("AAPL", 110)
    await b.submit_order(OrderRequest("AAPL", Side.BUY, 10))
    pos = b.positions["AAPL"]
    assert (pos.qty, pos.avg_price) == (20, 105)


async def test_partial_reduce_keeps_the_cost_basis():
    b = PaperBroker()
    b.mark("AAPL", 100)
    await b.submit_order(OrderRequest("AAPL", Side.BUY, 10))
    b.mark("AAPL", 110)
    await b.submit_order(OrderRequest("AAPL", Side.SELL, 4))
    pos = b.positions["AAPL"]
    assert (pos.qty, pos.avg_price) == (6, 100)
    (p,) = await b.get_positions()
    assert (p.market_value, p.unrealized_pnl) == (660, 60)
    assert b.cash == 100_000 - 1_000 + 440


async def test_flip_through_zero_restarts_cost_basis_at_fill_price():
    b = PaperBroker()
    b.mark("AAPL", 100)
    await b.submit_order(OrderRequest("AAPL", Side.BUY, 10))
    b.mark("AAPL", 90)
    await b.submit_order(OrderRequest("AAPL", Side.SELL, 15))
    pos = b.positions["AAPL"]
    assert (pos.qty, pos.avg_price) == (-5, 90)


async def test_closing_removes_the_position_and_returns_cash():
    b = PaperBroker(cash=1_000)
    b.mark("AAPL", 100)
    await b.submit_order(OrderRequest("AAPL", Side.BUY, 5))
    await b.submit_order(OrderRequest("AAPL", Side.SELL, 5))
    assert "AAPL" not in b.positions and b.cash == 1_000


async def test_sell_without_position_opens_a_short():
    b = PaperBroker()
    b.mark("AAPL", 50)
    await b.submit_order(OrderRequest("AAPL", Side.SELL, 5))
    pos = b.positions["AAPL"]
    assert (pos.qty, pos.avg_price, b.cash) == (-5, 50, 100_250)


async def test_non_marketable_limit_order_is_rejected_without_touching_positions():
    b = PaperBroker()
    b.mark("AAPL", 50)
    o = await b.submit_order(OrderRequest("AAPL", Side.BUY, 1, type=OrderType.LIMIT, limit_price=40))
    assert (o.status, o.filled_qty, o.avg_fill_price) == (OrderStatus.REJECTED, 0.0, None)
    assert b.positions == {} and b.cash == 100_000
    assert (await b.get_order(o.id)) is o


async def test_marketable_limit_order_fills_at_the_mark_not_the_limit():
    b = PaperBroker()
    b.mark("AAPL", 50)
    o = await b.submit_order(OrderRequest("AAPL", Side.BUY, 1, type=OrderType.LIMIT, limit_price=60))
    assert (o.status, o.avg_fill_price) == (OrderStatus.FILLED, 50)
    o2 = await b.submit_order(OrderRequest("AAPL", Side.SELL, 1, type=OrderType.LIMIT, limit_price=45))
    assert (o2.status, o2.avg_fill_price) == (OrderStatus.FILLED, 50)


async def test_order_without_mark_raises():
    with pytest.raises(RuntimeError):
        await PaperBroker().submit_order(OrderRequest("AAPL", Side.BUY, 1))


async def test_account_equity_marks_positions():
    b = PaperBroker()
    b.mark("AAPL", 100)
    await b.submit_order(OrderRequest("AAPL", Side.BUY, 10))
    b.mark("AAPL", 120)
    a = await b.get_account()
    assert (a.equity, a.cash, a.buying_power, a.currency) == (100_200, 99_000, 99_000, "USD")


async def test_order_updates_yield_each_order_once_in_order():
    b = PaperBroker()
    b.mark("AAPL", 10)
    o1 = await b.submit_order(OrderRequest("AAPL", Side.BUY, 1))
    o2 = await b.submit_order(OrderRequest("AAPL", Side.SELL, 1))
    gen = b.order_updates()
    assert (await gen.__anext__()) is o1
    assert (await gen.__anext__()) is o2
    await gen.aclose()


async def test_cancel_is_a_noop():
    b = PaperBroker()
    await b.cancel_order("nothing")
    await b.cancel_all()


async def test_replay_get_bars_filters_by_timeframe_start_and_end():
    hist = bars("AAPL", [1, 2, 3, 4], tf=Timeframe.M1) + bars("AAPL", [9], tf=Timeframe.M5)
    p = ReplayProvider([], history={"AAPL": hist})
    out = await p.get_bars("AAPL", Timeframe.M1, T0 + timedelta(minutes=1), end=T0 + timedelta(minutes=2))
    assert [b.close for b in out] == [2, 3]
    assert await p.get_bars("MSFT", Timeframe.M1, T0) == []


async def test_replay_stream_yields_events_in_given_order_and_ends():
    evs = bars("AAPL", [1, 2])
    p = ReplayProvider(evs)
    await p.subscribe(["AAPL"])
    assert [e async for e in p.stream()] == evs
    assert p.subscribed == ["AAPL"]
