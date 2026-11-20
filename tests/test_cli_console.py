"""The CLI's --db / --console wiring, end to end: the one console test that opens a real socket.

It binds to 127.0.0.1 on a free port, like the real thing, and talks to nothing else.
"""

import asyncio
import socket

import httpx
import pytest
from helpers import ticks, wave

from tradesignals import cli
from tradesignals.providers.fake import ReplayProvider
from tradesignals.store import Store


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def replay_feed(monkeypatch):
    """Register a replay provider under --data replay for the duration of a test."""
    data = ReplayProvider(ticks("AAPL", wave(120)), delay=0.01)
    data.name = "replay"
    monkeypatch.setitem(cli.DATA_PROVIDERS, "replay", lambda: data)
    monkeypatch.setattr(cli, "make_data", lambda name: data)
    monkeypatch.setenv("RISK_MAX_QUOTE_AGE_SEC", "1000000000")
    monkeypatch.delenv("TRADESIGNALS_DB", raising=False)
    monkeypatch.delenv("CONSOLE_PORT", raising=False)
    return data


def args_for(*extra: str):
    return cli.build_parser().parse_args(
        ["--data", "replay", "--broker", "paper", "--symbols", "aapl", "--execute", "--no-warmup", *extra]
    )


def test_parser_console_flags_default_off():
    args = cli.build_parser().parse_args(["--data", "alpaca", "--symbols", "AAPL"])
    assert (args.db, args.console, args.console_port) == (None, False, None)


async def test_db_flag_persists_a_run(replay_feed, tmp_path):
    db = tmp_path / "run.db"
    replay_feed._delay = 0
    await cli._run(args_for("--db", str(db)))
    store = Store(db)
    [run] = store.runs()
    assert run["data"] == "replay" and run["broker"] == "paper" and run["execute"] == 1 and run["symbols"] == "AAPL"
    assert run["stopped_at"] is not None and run["strategies"] == "sma_cross"
    counts = store.counts()
    assert counts["signals"] >= 3 and counts["orders"] >= 2 and counts["bars"] == 119


async def test_without_db_or_console_nothing_is_written(replay_feed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    replay_feed._delay = 0
    await cli._run(args_for())
    assert list(tmp_path.iterdir()) == []


async def test_console_flag_serves_the_console_while_the_engine_runs(replay_feed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # --console without --db uses data/tradesignals.db under the working directory
    monkeypatch.setenv("CONSOLE_TOKEN", "cli-test-token")
    port = free_port()
    run = asyncio.create_task(cli._run(args_for("--console", "--console-port", str(port))))
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=base, timeout=5) as c:
        for _ in range(100):  # wait for the server to come up
            try:
                status = (await c.get("/api/status")).json()
                break
            except httpx.TransportError:
                await asyncio.sleep(0.02)
        else:
            pytest.fail("the console did not start")
        assert status["mode"] == "paper" and status["running"] is True and status["data"] == "replay"
        page = await c.get("/")
        assert page.status_code == 200 and "content-security-policy" in page.headers and "Console token" in page.text
        assert (await c.get("/static/app.js")).status_code == 200  # package data is served
        # a real request with a foreign Host header is refused
        assert (await c.get("/api/status", headers={"host": "evil.example"})).status_code == 403
        assert (await c.post("/api/actions/halt", json={})).status_code == 401
        halted = await c.post(
            "/api/actions/halt", json={"reason": "cli test"}, headers={"x-console-token": "cli-test-token"}
        )
        assert halted.status_code == 200
    await asyncio.wait_for(run, timeout=15)  # the replay ends, the engine stops, the console shuts down
    with pytest.raises(httpx.TransportError):
        async with httpx.AsyncClient(base_url=base, timeout=1) as c:
            await c.get("/api/status")
    store = Store(tmp_path / cli.DEFAULT_CONSOLE_DB)
    assert store.runs()[0]["stopped_at"] is not None and store.audit_log()[0]["action"] == "halt"


async def test_console_port_in_use_does_not_stop_the_engine(replay_feed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    replay_feed._delay = 0
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        await cli._run(args_for("--console", "--console-port", str(busy.getsockname()[1])))
    assert Store(tmp_path / cli.DEFAULT_CONSOLE_DB).counts()["signals"] >= 3  # the run completed anyway


def test_console_extra_missing_is_a_clear_exit(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_console(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "console" and level == 1:
            raise ImportError("No module named 'fastapi'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_console)
    with pytest.raises(SystemExit, match="console extra"):
        cli._start_console(None, None, None, None, None)
