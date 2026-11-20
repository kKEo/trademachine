"""System page, hardening: logs, health, settings, retention, security headers (Phase 7)."""

import logging
from datetime import timedelta

from console_helpers import Rig, finished_run
from helpers import T0, bars, ticks
from test_controls import HOLDING

from tradesignals.console.app import filesize
from tradesignals.console.service import SECRET_SETTINGS, LogBuffer, _thin
from tradesignals.events import SnapshotEvent, StatusEvent
from tradesignals.models import Account, Timeframe, utcnow
from tradesignals.store import Store


# ---------------------------------------------------------------------- logs
async def test_log_tail_captures_tradesignals_loggers_only_and_filters_by_level():
    rig = Rig()
    rig.service.start(rig.writer)
    logging.getLogger("tradesignals.engine").info("engine says hello")
    logging.getLogger("tradesignals.xtb").warning("xtb ping failed: %s", "timeout")
    logging.getLogger("websockets.client").warning("> TEXT login frame with a password")  # third party
    tail = rig.service.log_tail()
    assert [r["message"] for r in tail[:2]] == ["xtb ping failed: timeout", "engine says hello"]  # newest first
    assert tail[0]["logger"] == "xtb" and tail[0]["level"] == "WARNING"
    assert not any("password" in r["message"] for r in tail)
    assert [r["message"] for r in rig.service.log_tail(level="WARNING")] == ["xtb ping failed: timeout"]
    assert len(rig.service.log_tail(level="nonsense")) == 2
    await rig.service.stop()
    logging.getLogger("tradesignals.engine").info("after stop")
    assert all(r["message"] != "after stop" for r in rig.service.logs.records)  # handler removed


def test_log_buffer_is_bounded_and_never_raises():
    buf = LogBuffer(capacity=3)
    for i in range(10):
        buf.emit(logging.LogRecord("tradesignals.x", logging.INFO, __file__, 1, "line %d", (i,), None))
    assert [r["message"] for r in buf.records] == ["line 7", "line 8", "line 9"]
    buf.emit(logging.LogRecord("tradesignals.x", logging.INFO, __file__, 1, "bad %d", ("not a number",), None))
    assert len(buf.records) == 3


# ---------------------------------------------------------------------- settings: no secret leaves the process
async def test_settings_show_secrets_only_as_set_or_not_set(monkeypatch):
    monkeypatch.setenv("ALPACA_SECRET_KEY", "sk-super-secret-value")
    monkeypatch.setenv("XTB_PASSWORD", "hunter2-password")
    monkeypatch.setenv("CONSOLE_TOKEN", "the-console-token")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.setenv("RISK_MAX_DAILY_LOSS", "321")
    async with finished_run() as rig, rig.client() as c:
        rows = {s["name"]: s for s in (await c.get("/api/system")).json()["settings"]}
        assert rows["ALPACA_SECRET_KEY"]["value"] == "set" and rows["ALPACA_API_KEY"]["value"] == "not set"
        assert rows["RISK_MAX_DAILY_LOSS"]["value"] == "321" and set(SECRET_SETTINGS) <= set(rows)
        for path in ("/system", "/api/system", "/api/logs", "/", "/risk", "/api/status"):
            body = (await c.get(path)).text
            for secret in ("sk-super-secret-value", "hunter2-password", "the-console-token"):
                assert secret not in body, (path, secret)


# ---------------------------------------------------------------------- health and page
async def test_system_page_and_health():
    async with finished_run(events=ticks("AAPL", HOLDING)) as rig, rig.client() as c:
        logging.getLogger("tradesignals.engine").warning("something to look at")
        page = (await c.get("/system")).text
        for text in ("Health", "Engine log", "Settings in effect", "Sessions", "something to look at", "in memory"):
            assert text in page
        assert "something to look at" in (await c.get("/system", params={"level": "warning"})).text
        health = (await c.get("/api/system")).json()["health"]
        assert health["database"]["rows"]["signals"] == 1 and health["failed_batches"] == 0
        assert {s["name"] for s in health["subscribers"]} >= {"console", "store"}
        assert health["late_events_dropped"] == 0 and "AAPL" in health["quote_age_sec"]
        assert (await c.get("/partials/health")).status_code == 200
        assert (await c.get("/partials/logs", params={"level": "WARNING"})).status_code == 200
        logs = (await c.get("/api/logs", params={"level": "WARNING"})).json()
        assert logs[0]["message"] == "something to look at"


async def test_late_events_and_dropped_events_are_visible():
    late = ticks("AAPL", [100.0, 101.0, 102.0])
    events = [late[0], late[2], late[1]]  # the middle tick arrives after its bar closed
    async with finished_run(events=events) as rig:
        health = await rig.service.health()
        assert health["late_events_dropped"] == 1
        slow = rig.bus.subscribe(maxsize=1, name="slow-tab")
        rig.bus.publish(StatusEvent("running"))
        rig.bus.publish(StatusEvent("running"))
        assert {"name": "slow-tab", "dropped": 1} in (await rig.service.health())["subscribers"]
        slow.close()


def test_filesize():
    assert filesize(None) == "in memory" and filesize(512) == "512 bytes"
    assert filesize(2048) == "2.0 KB" and filesize(5 * 1024**3) == "5.0 GB"


# ---------------------------------------------------------------------- failure isolation
async def test_a_failing_console_listener_does_not_stop_persistence_or_trading():
    rig = Rig()

    def boom(batch):
        raise RuntimeError("console bug")

    rig.writer.listeners.append(boom)
    await rig.run_engine()
    assert len(rig.engine.orders) >= 2 and rig.store.counts()["signals"] == len(rig.engine.signals)
    assert rig.writer.failed_batches == 0  # the rows were written; only the notification failed
    await rig.close()


async def test_a_failing_page_returns_500_and_the_engine_keeps_its_state():
    import httpx

    async with finished_run(events=ticks("AAPL", HOLDING)) as rig:

        async def boom(*a, **k):
            raise RuntimeError("template bug")

        rig.service.kpis = boom
        transport = httpx.ASGITransport(app=rig.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8788") as c:
            assert (await c.get("/")).status_code == 500
            assert (await c.get("/api/status")).status_code == 200  # other pages still answer
        assert len(rig.engine.positions) == 1


# ---------------------------------------------------------------------- retention
def test_prune_drops_old_equity_points_and_intraday_bars_but_keeps_daily_bars(tmp_path):
    store = Store(tmp_path / "ts.db")
    run = store.start_run()
    now = utcnow()
    old, recent = now - timedelta(days=400), now - timedelta(days=5)
    store.apply(run, [SnapshotEvent(Account(1, 1, 1), (), ts=t) for t in (old, now - timedelta(days=100), recent)])
    store.put_bars(bars("AAPL", [1.0], start=old) + bars("AAPL", [2.0], start=recent))
    store.put_bars(bars("AAPL", [3.0], tf=Timeframe.D1, start=old))
    assert store.prune(equity_days=90, bar_days=365) == {"equity_snapshots": 2, "bars": 1}
    assert [b["close"] for b in store.bars("AAPL", "1m")] == [2.0] and len(store.bars("AAPL", "1d")) == 1
    assert store.counts()["equity_snapshots"] == 1 and store.size_bytes() > 0
    assert store.prune() == {"equity_snapshots": 0, "bars": 0}
    assert Store().size_bytes() is None


def test_equity_peak_and_first_of_day():
    store = Store()
    run, other = store.start_run(), store.start_run()
    store.apply(
        run,
        [
            SnapshotEvent(Account(v, v, v), (), ts=T0 + timedelta(minutes=i))
            for i, v in enumerate([100.0, 130.0, 110.0])
        ],
    )
    store.apply(other, [SnapshotEvent(Account(500.0, 500.0, 500.0), (), ts=T0 + timedelta(hours=1))])
    assert store.equity_peak(run) == 130.0 and store.equity_peak() == 500.0 and store.equity_peak("nope") is None
    assert (
        store.equity_first(T0.timestamp()) == 100.0
        and store.equity_first((T0 + timedelta(minutes=2)).timestamp()) == 110.0
    )
    assert store.equity_first((T0 + timedelta(days=1)).timestamp()) is None


def test_long_equity_series_is_thinned_for_the_chart():
    points = [{"time": i, "value": float(i)} for i in range(10_000)]
    thin = _thin(points, limit=100)
    assert len(thin) == 100 and thin[-1] == points[-1] and thin[0]["time"] == 99
    assert [p["time"] for p in thin] == sorted(p["time"] for p in thin)
    assert _thin(points[:50], limit=100) == points[:50]


# ---------------------------------------------------------------------- security headers
async def test_every_response_carries_security_headers():
    async with finished_run() as rig, rig.client() as c:
        for path in ("/", "/api/status", "/static/app.css", "/nope"):
            r = await c.get(path)
            csp = r.headers["content-security-policy"]
            assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
            assert "script-src" not in csp  # scripts fall back to default-src 'self': no inline, no eval
            assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
            assert r.headers["referrer-policy"] == "no-referrer"
        assert (await c.get("/")).headers["cache-control"] == "no-store"
        assert "no-store" not in (await c.get("/static/app.css")).headers.get("cache-control", "")
        refused = await c.get("/", headers={"host": "evil.example"})
        assert refused.status_code == 403 and "content-security-policy" in refused.headers


async def test_pages_contain_no_inline_script_or_event_handlers():
    """The content security policy forbids inline script; the pages must not need any."""
    import re

    async with (
        finished_run(events=ticks("AAPL", HOLDING), token="t") as rig,
        rig.client(headers={"x-console-token": "t"}) as c,
    ):
        sid = rig.store.signals()[0]["id"]
        for path in (
            "/",
            "/portfolio",
            "/markets",
            "/markets/AAPL",
            "/signals",
            f"/signals/{sid}",
            "/orders",
            "/strategies",
            "/risk",
            "/system",
        ):
            html = (await c.get(path)).text
            assert not re.search(r"<script(?![^>]*\bsrc=)", html), path
            assert not re.search(r"\son[a-z]+\s*=", html), path
            assert "hx-on" not in html and "javascript:" not in html, path
