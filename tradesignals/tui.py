"""Terminal view of the admin console: one screen, a few keys.

A thin client of the console's JSON API. It holds no trading logic and never talks to a broker;
everything it shows and does goes through the HTTP API of a running engine
(``tradesignals ... --console``).

    tradesignals-tui                       # http://127.0.0.1:$CONSOLE_PORT (default 8788)
    tradesignals-tui --url http://127.0.0.1:8900

Actions (halt, resume, kill switch) need the console token; it is read from ``CONSOLE_TOKEN``
in the environment or ``.env``, never from the command line.

Needs the ``tui`` extra: pip install -e ".[tui]"
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, Static

DEFAULT_PORT = 8788
DASH = "—"
CONFIRM_KILL = "STOP"


def money(v: Any) -> str:
    return DASH if v is None else f"{v:,.2f}"


def signed(v: Any, suffix: str = "") -> str:
    if v is None:
        return DASH
    if round(v, 2) == 0:
        return f"0.00{suffix}"
    return f"{v:+,.2f}{suffix}"


def qty(v: Any) -> str:
    return DASH if v is None else f"{v:,.4f}".rstrip("0").rstrip(".")


def clock(ts: Any) -> str:
    return DASH if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")


def tone(v: Any) -> str:
    """Rich style for a signed number."""
    if v is None or abs(v) < 1e-9:
        return ""
    return "green" if v > 0 else "red"


def styled(text: str, style: str) -> str:
    return f"[{style}]{text}[/]" if style else text


OUTCOMES = {
    "order": "Order sent",
    "no_op": "Nothing to do",
    "risk_reject": "Blocked by risk",
    "submit_failed": "Order failed",
    None: "Not traded",
}


class ConsoleClient:
    """The few console API calls the terminal view needs."""

    def __init__(self, url: str, token: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self.token = token or None
        headers = {"x-console-token": self.token} if self.token else {}
        self._http = httpx.AsyncClient(base_url=self.url, headers=headers, timeout=5.0, transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def snapshot(self) -> dict[str, Any]:
        """Everything the screen shows, in three requests."""
        overview = (await self._http.get("/api/overview")).raise_for_status().json()
        signals = (await self._http.get("/api/signals", params={"limit": 12})).raise_for_status().json()
        orders = (await self._http.get("/api/orders", params={"limit": 12})).raise_for_status().json()
        return {**overview, "signals": signals, "orders": orders}

    async def act(self, action: str, **body: Any) -> tuple[bool, str]:
        """Run a console action. Returns (worked, what to tell the operator)."""
        r = await self._http.post(f"/api/actions/{action}", json=body)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.is_success:
            return True, data.get("message", "Done.")
        return False, data.get("error") or f"The console refused the request ({r.status_code})."


class Confirm(ModalScreen[bool]):
    """A yes/no question, or a typed word for actions that cannot be undone."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str, confirm_label: str, word: str | None = None):
        super().__init__()
        self.question = question
        self.confirm_label = confirm_label
        self.word = word

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question")
            if self.word:
                yield Input(placeholder=f"Type {self.word} to confirm", id="word")
            with Horizontal(id="buttons"):
                yield Button(self.confirm_label, variant="error", id="yes")
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        self.query_one("#word" if self.word else "#no").focus()

    def _confirmed(self) -> bool:
        return not self.word or self.query_one("#word", Input).value.strip() == self.word

    @on(Button.Pressed, "#yes")
    def yes(self) -> None:
        if self._confirmed():
            self.dismiss(True)
        else:
            self.query_one("#question", Label).update(f"{self.question}\nType {self.word} exactly to confirm.")
            self.query_one("#word", Input).focus()  # back to the field, ready for another try

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.yes()

    @on(Button.Pressed, "#no")
    def no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConsoleTui(App[None]):
    TITLE = "tradesignals"
    CSS = """
    #status { height: 1; padding: 0 1; background: $boost; }
    #figures { height: 1; padding: 0 1; }
    #quotes { height: 1; padding: 0 1; color: $text-muted; }
    #notice { height: 1; padding: 0 1; }
    .title { height: 1; padding: 0 1; text-style: bold; margin-top: 1; }
    DataTable { height: auto; max-height: 12; }
    #positions { max-height: 8; }
    #dialog { width: 64; height: auto; padding: 1 2; border: thick $error; background: $surface; }
    Confirm { align: center middle; }
    #buttons { height: auto; margin-top: 1; }
    #buttons Button { margin-right: 2; }
    """
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("h", "halt", "Halt / resume"),
        Binding("k", "kill", "Kill switch"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, client: ConsoleClient, interval: float = 2.0):
        super().__init__()
        self.client = client
        self.interval = interval
        self.data: dict[str, Any] | None = None
        self.connected = False

    def compose(self) -> ComposeResult:
        yield Static("Connecting to the console", id="status")
        yield Static("", id="figures")
        yield Static("", id="quotes")
        yield Static("", id="notice")
        yield Static("Positions", classes="title")
        yield DataTable(id="positions", cursor_type="none", zebra_stripes=True)
        yield Static("Latest signals", classes="title")
        yield DataTable(id="signals", cursor_type="none", zebra_stripes=True)
        yield Static("Latest orders", classes="title")
        yield DataTable(id="orders", cursor_type="none", zebra_stripes=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#positions", DataTable).add_columns("Symbol", "Shares", "Avg cost", "Last", "Open P&L")
        self.query_one("#signals", DataTable).add_columns("Time", "Symbol", "Signal", "Strategy", "Result")
        self.query_one("#orders", DataTable).add_columns("Time", "Symbol", "Side", "Shares", "Fill price", "Status")
        await self.refresh_data()
        self.set_interval(self.interval, self.refresh_data)

    async def on_unmount(self) -> None:
        await self.client.close()

    # ------------------------------------------------------------------ data
    async def refresh_data(self) -> None:
        try:
            self.data = await self.client.snapshot()
        except (httpx.HTTPError, ValueError) as e:
            self.connected = False
            self.query_one("#status", Static).update(
                f"[yellow]Cannot reach the console at {self.client.url}[/] ({type(e).__name__}). "
                "Is the engine running with --console?"
            )
            return
        self.connected = True
        self.render_data(self.data)

    def render_data(self, d: dict[str, Any]) -> None:
        st, k = d["status"], d["kpis"]
        mode = {"paper": "[cyan]Paper trading[/]", "live": "[bold red]Live trading[/]"}.get(st["mode"], "Signals only")
        engine = "running" if st["running"] else "[yellow]stopped[/]"
        halt = f"   [bold yellow]Trading halted: {st['halt_reason']}[/]" if st["halted"] else ""
        feed = st["data"] + (f" to {st['broker']}" if st["broker"] else "")
        self.query_one("#status", Static).update(f"{mode}   Engine {engine}   Feed {feed}{halt}")

        if k["has_account"]:
            self.query_one("#figures", Static).update(
                f"Equity [bold]{money(k['equity'])}[/] {k['currency']}   "
                f"Today {styled(signed(k['day_pnl']), tone(k['day_pnl']))}   "
                f"From peak {styled(signed(k['drawdown_pct'], '%'), tone(k['drawdown_pct']))}   "
                f"Open P&L {styled(signed(k['unrealized_pnl']), tone(k['unrealized_pnl']))}   "
                f"Cash {money(k['cash'])}"
            )
        else:
            self.query_one("#figures", Static).update("No broker connected: signals only, no account to show.")

        self.query_one("#quotes", Static).update(
            "   ".join(f"{q['symbol']} {money(q.get('price'))}" for q in d["quotes"]) or "No quotes yet"
        )

        self._fill(
            "#positions",
            [
                (
                    p["symbol"],
                    qty(p["qty"]),
                    money(p["avg_price"]),
                    money(p["last"]),
                    styled(signed(p["unrealized_pnl"]), tone(p["unrealized_pnl"])),
                )
                for p in d["positions"]
            ],
        )
        self._fill(
            "#signals",
            [
                (clock(s["ts"]), s["symbol"], s["kind"], s["strategy"], OUTCOMES.get(s.get("outcome"), "Not traded"))
                for s in d["signals"]
            ],
        )
        self._fill(
            "#orders",
            [
                (
                    clock(o["ts"]),
                    o["symbol"],
                    o["side"],
                    qty(o["filled_qty"]),
                    money(o["avg_fill_price"]),
                    o["status"].replace("_", " "),
                )
                for o in d["orders"]
            ],
        )

    def _fill(self, selector: str, rows: list[tuple[str, ...]]) -> None:
        table = self.query_one(selector, DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)

    def say(self, text: str, ok: bool = True) -> None:
        self.query_one("#notice", Static).update(text if ok else f"[red]{text}[/]")

    # ------------------------------------------------------------------ actions
    async def action_refresh(self) -> None:
        await self.refresh_data()

    def _can_act(self) -> bool:
        if not self.connected or self.data is None:
            self.say("Not connected to the console.", ok=False)
            return False
        if not self.client.token:
            self.say("View only: set CONSOLE_TOKEN (the engine's value) to use actions.", ok=False)
            return False
        return True

    def action_halt(self) -> None:
        if not self._can_act():
            return
        assert self.data is not None
        halted = self.data["status"]["halted"]
        question = (
            "Resume trading? Strategies may open positions again."
            if halted
            else "Halt trading? No position can be opened or increased; exits still go through."
        )

        async def done(yes: bool | None) -> None:
            if yes:
                await self._run(
                    "resume" if halted else "halt", **({} if halted else {"reason": "halted from the terminal"})
                )

        self.push_screen(Confirm(question, "Resume trading" if halted else "Halt trading"), done)

    def action_kill(self) -> None:
        if not self._can_act():
            return

        async def done(yes: bool | None) -> None:
            if yes:
                await self._run("kill-switch", confirm=CONFIRM_KILL)

        self.push_screen(
            Confirm(
                "Kill switch: halt trading, cancel open orders and stop the engine.\nPositions stay open.",
                "Stop the engine",
                word=CONFIRM_KILL,
            ),
            done,
        )

    async def _run(self, action: str, **body: Any) -> None:
        try:
            ok, message = await self.client.act(action, **body)
        except httpx.HTTPError as e:
            ok, message = False, f"The console did not answer ({type(e).__name__})."
        self.say(message, ok)
        await self.refresh_data()


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    port = os.environ.get("CONSOLE_PORT") or DEFAULT_PORT
    ap = argparse.ArgumentParser(
        prog="tradesignals-tui", description="Terminal view of the tradesignals admin console."
    )
    ap.add_argument("--url", default=f"http://127.0.0.1:{port}", help="console address (default: %(default)s)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes (default: %(default)s)")
    args = ap.parse_args()
    ConsoleTui(ConsoleClient(args.url, token=os.environ.get("CONSOLE_TOKEN")), interval=args.interval).run()


if __name__ == "__main__":
    main()
