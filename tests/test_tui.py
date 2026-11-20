"""Terminal UI: a Textual pilot drives the app against the console's ASGI app, in process (Phase 6)."""

import asyncio

import httpx
from console_helpers import BASE, Rig, finished_run
from helpers import ticks
from test_controls import HOLDING, TOKEN

from tradesignals.tui import Confirm, ConsoleClient, ConsoleTui, clock, money, qty, signed, styled, tone


def tui_for(rig, token=TOKEN, **kw) -> ConsoleTui:
    client = ConsoleClient(BASE, token=token, transport=httpx.ASGITransport(app=rig.app))
    return ConsoleTui(client, interval=3600, **kw)


def text_of(app, selector: str) -> str:
    return str(app.query_one(selector).render())


def rows_of(app, selector: str) -> list[list[str]]:
    table = app.query_one(selector)
    return [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]


def test_formatters():
    assert money(1234.5) == "1,234.50" and money(None) == "—" and qty(3.0) == "3"
    assert signed(1.5) == "+1.50" and signed(-0.001) == "0.00" and signed(2, "%") == "+2.00%" and signed(None) == "—"
    assert (
        (tone(1), tone(-1), tone(0)) == ("green", "red", "")
        and styled("x", "") == "x"
        and styled("x", "red") == "[red]x[/]"
    )
    assert clock(0) == "00:00:00" and clock(None) == "—"


async def test_screen_shows_status_figures_positions_signals_and_orders():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig:
        app = tui_for(rig)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "Paper trading" in text_of(app, "#status") and "Engine" in text_of(app, "#status")
            assert "Equity" in text_of(app, "#figures") and "AAPL" in text_of(app, "#quotes")
            [position] = rows_of(app, "#positions")
            assert position[0] == "AAPL" and position[1] == "3"
            [signal] = rows_of(app, "#signals")
            assert signal[1:] == ["AAPL", "long", "sma_cross", "Order sent"]
            [order] = rows_of(app, "#orders")
            assert order[1:4] == ["AAPL", "buy", "3"] and order[5] == "filled"


async def test_signal_only_engine_and_refresh_key():
    async with finished_run(broker=False, execute=False) as rig:
        app = tui_for(rig, token=None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "Signals only" in text_of(app, "#status") and "No broker connected" in text_of(app, "#figures")
            assert rows_of(app, "#positions") == [] and rows_of(app, "#signals")[0][4] == "Not traded"
            await pilot.press("r")
            await pilot.pause()
            assert app.connected


async def test_halt_and_resume_keys_ask_first():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig:
        app = tui_for(rig)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert isinstance(app.screen, Confirm)
            await pilot.press("escape")  # changed my mind
            await pilot.pause()
            assert not rig.engine.halted

            await pilot.press("h")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()
            assert rig.engine.halted and rig.risk.halt_reason == "halted from the terminal"
            assert "Trading halted" in text_of(app, "#status") and "Trading halted" in text_of(app, "#notice")

            await pilot.press("h")  # now the same key offers to resume
            await pilot.pause()
            assert "Resume trading?" in str(app.screen.query_one("#question").render())
            await pilot.click("#yes")
            await pilot.pause()
            assert not rig.engine.halted and "Trading resumed" in text_of(app, "#notice")
        assert [a["action"] for a in rig.store.audit_log()][::-1] == ["halt", "resume"]


async def test_kill_switch_needs_the_typed_word():
    rig = Rig(events=ticks("AAPL", [100.0] * 2000), token=TOKEN)
    rig.data._delay = 0.005
    rig.writer.start()
    rig.service.start(rig.writer)
    run = asyncio.create_task(rig.engine.run())
    await asyncio.sleep(0.05)
    app = tui_for(rig)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        await pilot.click("#yes")  # nothing typed
        await pilot.pause()
        assert isinstance(app.screen, Confirm) and rig.engine.running
        assert "exactly" in str(app.screen.query_one("#question").render())
        await pilot.press(*"STOP", "enter")
        await pilot.pause()
        await asyncio.wait_for(run, timeout=2)
        assert not rig.engine.running and rig.engine.halted
        assert "Kill switch" in text_of(app, "#notice")
    await rig.close()


async def test_without_a_token_actions_are_refused_locally():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig:
        app = tui_for(rig, token=None)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert not isinstance(app.screen, Confirm) and "View only" in text_of(app, "#notice")
            await pilot.press("k")
            await pilot.pause()
            assert not isinstance(app.screen, Confirm) and not rig.engine.halted


async def test_wrong_token_is_reported_not_raised():
    async with finished_run(events=ticks("AAPL", HOLDING), token=TOKEN) as rig:
        app = tui_for(rig, token="wrong")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            await pilot.click("#yes")
            await pilot.pause()
            assert "Unlock the console" in text_of(app, "#notice") and not rig.engine.halted


async def test_unreachable_console_is_explained():
    def refuse(request):
        raise httpx.ConnectError("refused")

    client = ConsoleClient(BASE, transport=httpx.MockTransport(refuse))
    app = ConsoleTui(client, interval=3600)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert "Cannot reach the console" in text_of(app, "#status") and not app.connected
        await pilot.press("h")
        await pilot.pause()
        assert "Not connected" in text_of(app, "#notice")
