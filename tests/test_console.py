"""Admin console, read-only part: pages, JSON API, live events and the access guard."""

import asyncio

import pytest
from console_helpers import Rig, finished_run
from helpers import ticks

from tradesignals.console import api, sse
from tradesignals.console.app import clock, duration, money, pct, qty, signed, tone
from tradesignals.console.security import host_name, is_loopback, token_ok
from tradesignals.console.server import HOST, ConsoleServer


# ---------------------------------------------------------------------- formatting
def test_number_filters():
    assert money(1234.5) == "1,234.50" and money(None) == "—"
    assert signed(12.3) == "+12.30" and signed(-12.3) == "−12.30"
    assert pct(-0.5) == "−0.50%"
    assert qty(3.0) == "3" and qty(2.5) == "2.5"
    assert (tone(1), tone(-1), tone(0), tone(None)) == ("up", "down", "", "")
    assert duration(59) == "59s" and duration(3700) == "1h 01m" and duration(90000) == "1d 1h"
    assert clock(None) == "—"


# ---------------------------------------------------------------------- pages
@pytest.mark.parametrize("path", ["/", "/portfolio", "/orders"])
async def test_pages_render_with_run_history(path):
    async with finished_run() as rig, rig.client() as c:
        r = await c.get(path)
        assert r.status_code == 200
        assert "tradesignals" in r.text and 'data-mode="paper"' in r.text
        assert 'data-symbol="AAPL"' in r.text  # the quote tape


async def test_overview_shows_signals_orders_and_account():
    async with finished_run() as rig, rig.client() as c:
        html = (await c.get("/")).text
        assert "Order sent" in html and "Nothing to do" in html  # signal outcomes
        assert "filled" in html
        assert "Equity" in html and "Paper trading" in html


async def test_signal_only_console_explains_the_missing_account():
    async with finished_run(broker=False, execute=False) as rig, rig.client() as c:
        html = (await c.get("/")).text
        assert "Signals only" in html and "No broker is connected" in html
        assert "Not traded" in html
        assert (await c.get("/orders")).text.count("signals-only mode") == 1


@pytest.mark.parametrize(
    "path", ["/partials/status", "/partials/kpis", "/partials/positions", "/partials/signals", "/partials/orders"]
)
async def test_partials_are_fragments(path):
    async with finished_run() as rig, rig.client() as c:
        r = await c.get(path)
        assert r.status_code == 200 and "<html" not in r.text


# ---------------------------------------------------------------------- JSON API
async def test_api_status_and_overview():
    async with finished_run() as rig, rig.client() as c:
        status = (await c.get("/api/status")).json()
        assert status["mode"] == "paper" and status["symbols"] == ["AAPL"]
        assert status["strategies"][0]["name"] == "sma_cross"
        over = (await c.get("/api/overview")).json()
        account = await rig.broker.get_account()
        assert over["kpis"]["equity"] == account.equity
        assert over["quotes"][0]["symbol"] == "AAPL" and over["quotes"][0]["price"] is not None


async def test_api_signals_orders_and_equity_match_the_engine():
    async with finished_run() as rig, rig.client() as c:
        signals = (await c.get("/api/signals", params={"limit": 500})).json()
        assert len(signals) == len(rig.engine.signals)
        assert [s["ts"] for s in signals] == sorted((s["ts"] for s in signals), reverse=True)
        assert len((await c.get("/api/signals", params={"kind": "long"})).json()) < len(signals)
        orders = (await c.get("/api/orders")).json()
        assert len(orders) == len(rig.engine.orders) and orders[0]["status"] == "filled"
        equity = (await c.get("/api/equity")).json()
        assert equity and set(equity[0]) == {"time", "value"}
        assert [p["time"] for p in equity] == sorted({p["time"] for p in equity})  # strictly increasing


async def test_api_positions_are_enriched_with_last_price_and_share():
    # prices rise at the end, so the run finishes holding a position
    prices = [100.0] * 20 + [100 + i for i in range(1, 30)]
    async with finished_run(events=ticks("AAPL", prices)) as rig, rig.client() as c:
        [p] = (await c.get("/api/positions")).json()
        assert p["symbol"] == "AAPL" and p["qty"] == 3 and p["last"] == prices[-1]
        assert p["share_pct"] == 100 and p["unrealized_pnl"] > 0 and p["traded"] is True


async def test_api_rejects_out_of_range_limit():
    async with finished_run() as rig, rig.client() as c:
        assert (await c.get("/api/signals", params={"limit": 100000})).status_code == 422


# ---------------------------------------------------------------------- live events
async def test_sse_delivers_a_signal_after_it_is_stored():
    rig = Rig()
    rig.writer.start()
    rig.service.start(rig.writer)
    seen: list[str] = []

    async def listen():
        async for chunk in sse.stream(rig.service, keepalive=5):
            seen.append(chunk.split("\n", 1)[0])
            if chunk.startswith("event: bye"):
                return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0)
    await rig.engine.run()
    await rig.flush()
    # the notification arrives only once the row is queryable
    assert "event: signal" in seen and rig.store.counts()["signals"] == len(rig.engine.signals)
    await rig.close()
    await asyncio.wait_for(listener, timeout=2)
    assert seen[0] == "event: hello" and seen[-1] == "event: bye"
    assert {"event: quote", "event: bar", "event: order", "event: snapshot", "event: status"} <= set(seen)
    assert not rig.service._clients  # the connection was released


async def test_sse_keepalive_and_slow_client_does_not_block():
    rig = Rig()
    gen = sse.stream(rig.service, keepalive=0.01)
    assert (await gen.__anext__()).startswith("event: hello")
    assert (await gen.__anext__()).startswith(": keep-alive")
    for _ in range(2000):  # far more than a client queue holds; must not raise or block
        rig.service._broadcast(type("N", (), {"name": "status", "data": {}})())
    await gen.aclose()


async def test_events_endpoint_is_an_event_stream():
    rig = Rig()
    response = await api.events(type("Req", (), {"app": rig.app})())
    assert response.media_type == "text/event-stream"


# ---------------------------------------------------------------------- access guard
def test_host_helpers():
    assert host_name("127.0.0.1:8788") == "127.0.0.1" and host_name("[::1]:8788") == "[::1]"
    assert is_loopback("localhost:1") and is_loopback("LOCALHOST") and not is_loopback("evil.example:8788")
    assert not is_loopback("127.0.0.1.evil.example")
    assert token_ok("abc", "abc") and not token_ok("abc", "abd") and not token_ok(None, "x") and not token_ok("x", None)


async def test_requests_with_a_foreign_host_header_are_refused():
    """DNS rebinding: a hostile page reaches 127.0.0.1 under its own name."""
    async with finished_run() as rig, rig.client() as c:
        for path in ("/", "/api/status", "/static/app.css"):
            assert (await c.get(path, headers={"host": "evil.example:8788"})).status_code == 403
        assert (await c.get("/api/status", headers={"host": "localhost:8788"})).status_code == 200


async def test_console_without_token_is_read_only():
    async with finished_run() as rig, rig.client() as c:
        r = await c.post("/api/actions/kill-switch")
        assert r.status_code == 403 and "read-only" in r.json()["error"]


async def test_state_changing_requests_need_same_origin_and_token():
    async with finished_run(token="s3cret") as rig, rig.client() as c:
        assert (await c.post("/api/actions/nothing", headers={"origin": "http://evil.example"})).status_code == 403
        assert (await c.post("/api/actions/nothing")).status_code == 401
        assert (await c.post("/api/actions/nothing", headers={"x-console-token": "wrong"})).status_code == 401
        # with the right token the guard lets the request through (to a route that does not exist)
        assert (await c.post("/api/actions/nothing", headers={"x-console-token": "s3cret"})).status_code == 404


def test_server_binds_to_loopback_only():
    server = ConsoleServer(app=lambda *a: None, port=8788)
    assert HOST == "127.0.0.1" and server._server.config.host == "127.0.0.1"
    assert server.url == "http://127.0.0.1:8788"


async def test_server_start_failure_does_not_raise():
    """A console that cannot start must not take the engine down."""
    server = ConsoleServer(app=lambda *a: None, port=8788)

    async def boom():
        raise SystemExit(3)  # what uvicorn does when the port is taken

    server._server.serve = boom
    server.start()
    await server.stop()
