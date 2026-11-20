"""SQLite persistence for signals, orders, bars and equity (standard library only).

``Store`` owns the schema and the queries; all its methods are synchronous and thread-safe.
``StoreWriter`` subscribes to the event bus and writes batches from a worker thread, so the
engine's event loop never waits for the disk and a failing database never stops trading.

Timestamps are stored as UTC epoch seconds (REAL). The engine does not import this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .events import (
    BarEvent,
    Event,
    EventBus,
    OrderEvent,
    SignalEvent,
    SignalOutcome,
    SnapshotEvent,
)
from .models import Bar, OrderStatus, Timeframe, utcnow

log = logging.getLogger("tradesignals.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    stopped_at REAL,
    data TEXT NOT NULL DEFAULT '',
    broker TEXT NOT NULL DEFAULT '',
    paper INTEGER,
    execute INTEGER NOT NULL DEFAULT 0,
    symbols TEXT NOT NULL DEFAULT '',
    strategies TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    strategy TEXT NOT NULL,
    strength REAL NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    timeframe TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS signals_ts ON signals (ts);
CREATE INDEX IF NOT EXISTS signals_symbol_ts ON signals (symbol, ts);
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    signal_id TEXT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    client_order_id TEXT
);
CREATE INDEX IF NOT EXISTS outcomes_signal ON signal_outcomes (signal_id);
CREATE TABLE IF NOT EXISTS orders (
    source TEXT NOT NULL,
    id TEXT NOT NULL,
    client_order_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL,
    signal_id TEXT,
    origin TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    status TEXT NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    ts REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (source, id)
);
CREATE INDEX IF NOT EXISTS orders_ts ON orders (ts);
CREATE INDEX IF NOT EXISTS orders_client ON orders (client_order_id);
CREATE TABLE IF NOT EXISTS order_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    order_id TEXT NOT NULL,
    ts REAL NOT NULL,
    status TEXT NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL
);
CREATE INDEX IF NOT EXISTS order_updates_order ON order_updates (source, order_id);
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start REAL NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (symbol, timeframe, start)
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    equity REAL,
    cash REAL,
    buying_power REAL,
    currency TEXT,
    positions TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS equity_ts ON equity_snapshots (ts);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT ''
);
"""


#: Columns added after the first release: (table, column, declaration). Applied to older files on open.
MIGRATIONS = [
    ("signals", "timeframe", "TEXT NOT NULL DEFAULT ''"),
]


def to_epoch(dt: datetime) -> float:
    return dt.timestamp()


def from_epoch(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def bar_from_row(row: dict[str, Any]) -> Bar:
    return Bar(
        row["symbol"],
        Timeframe(row["timeframe"]),
        row["open"],
        row["high"],
        row["low"],
        row["close"],
        row["volume"],
        from_epoch(row["start"]),
        row["source"],
    )


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)
            for table, column, decl in MIGRATIONS:
                have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                if column not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ helpers
    def _all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, params).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ runs
    def start_run(
        self,
        data: str = "",
        broker: str = "",
        paper: bool | None = None,
        execute: bool = False,
        symbols: Iterable[str] = (),
        strategies: Iterable[str] = (),
    ) -> str:
        run_id = uuid4().hex[:12]
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO runs (id, started_at, data, broker, paper, execute, symbols, strategies) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    to_epoch(utcnow()),
                    data,
                    broker,
                    None if paper is None else int(paper),
                    int(execute),
                    ",".join(symbols),
                    ",".join(strategies),
                ),
            )
        return run_id

    def end_run(self, run_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE runs SET stopped_at=? WHERE id=?", (to_epoch(utcnow()), run_id))

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ writes
    def apply(self, run_id: str, events: Sequence[Event]) -> None:
        """Persist a batch of bus events in one transaction. Unknown event types are ignored."""
        with self._lock, self._db:
            for ev in events:
                if isinstance(ev, SignalEvent):
                    self._put_signal(run_id, ev)
                elif isinstance(ev, SignalOutcome):
                    self._db.execute(
                        "INSERT INTO signal_outcomes (run_id, signal_id, ts, kind, detail, client_order_id) "
                        "VALUES (?,?,?,?,?,?)",
                        (run_id, ev.signal_id, to_epoch(ev.ts), ev.kind.value, ev.detail, ev.client_order_id),
                    )
                elif isinstance(ev, OrderEvent):
                    self._put_order(run_id, ev)
                elif isinstance(ev, BarEvent):
                    self._put_bar(ev.bar)
                elif isinstance(ev, SnapshotEvent):
                    self._put_snapshot(run_id, ev)

    def _put_signal(self, run_id: str, ev: SignalEvent) -> None:
        s = ev.signal
        self._db.execute(
            "INSERT OR REPLACE INTO signals (id, run_id, ts, symbol, kind, strategy, strength, reason, timeframe) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ev.signal_id,
                run_id,
                to_epoch(s.ts),
                s.symbol,
                s.kind.value,
                s.strategy,
                s.strength,
                s.reason,
                ev.timeframe,
            ),
        )

    def _put_order(self, run_id: str, ev: OrderEvent) -> None:
        o = ev.order
        now = to_epoch(ev.ts)
        cur = self._db.execute("SELECT * FROM orders WHERE source=? AND id=?", (o.source, o.id)).fetchone()
        if cur is None:
            self._db.execute(
                "INSERT INTO orders (source, id, client_order_id, run_id, signal_id, origin, symbol, side, qty, "
                "status, filled_qty, avg_fill_price, ts, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    o.source,
                    o.id,
                    o.client_order_id or "",
                    run_id,
                    ev.signal_id,
                    ev.origin or ("" if not ev.update else "external"),
                    o.symbol,
                    o.side.value,
                    o.qty,
                    o.status.value,
                    o.filled_qty,
                    o.avg_fill_price,
                    to_epoch(o.ts),
                    now,
                ),
            )
        else:
            # A push update can overtake the reply to our own submit. Never let the older view
            # (the submit reply) overwrite a terminal state, and never lose who caused the order.
            keep_state = OrderStatus(cur["status"]).is_terminal and not ev.update
            origin = ev.origin if ev.origin else cur["origin"]
            if cur["origin"] == "external" and ev.origin:
                origin = ev.origin
            self._db.execute(
                "UPDATE orders SET origin=?, signal_id=COALESCE(?, signal_id), status=?, filled_qty=?, "
                "avg_fill_price=?, updated_at=? WHERE source=? AND id=?",
                (
                    origin,
                    ev.signal_id,
                    cur["status"] if keep_state else o.status.value,
                    cur["filled_qty"] if keep_state else o.filled_qty,
                    cur["avg_fill_price"] if keep_state else o.avg_fill_price,
                    now,
                    o.source,
                    o.id,
                ),
            )
        self._db.execute(
            "INSERT INTO order_updates (source, order_id, ts, status, filled_qty, avg_fill_price) VALUES (?,?,?,?,?,?)",
            (o.source, o.id, now, o.status.value, o.filled_qty, o.avg_fill_price),
        )

    def _put_bar(self, b: Bar) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO bars (symbol, timeframe, start, open, high, low, close, volume, source) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (b.symbol, b.timeframe.value, to_epoch(b.start), b.open, b.high, b.low, b.close, b.volume, b.source),
        )

    def put_bars(self, bars: Iterable[Bar]) -> None:
        with self._lock, self._db:
            for b in bars:
                self._put_bar(b)

    def _put_snapshot(self, run_id: str, ev: SnapshotEvent) -> None:
        a = ev.account
        self._db.execute(
            "INSERT INTO equity_snapshots (run_id, ts, equity, cash, buying_power, currency, positions) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                run_id,
                to_epoch(ev.ts),
                a.equity if a else None,
                a.cash if a else None,
                a.buying_power if a else None,
                a.currency if a else None,
                json.dumps([asdict(p) for p in ev.positions]),
            ),
        )

    def audit(self, action: str, detail: str = "", result: str = "", run_id: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO audit_log (run_id, ts, action, detail, result) VALUES (?,?,?,?,?)",
                (run_id, to_epoch(utcnow()), action, detail, result),
            )

    # ------------------------------------------------------------------ queries
    def signals(
        self,
        limit: int = 100,
        offset: int = 0,
        symbol: str | None = None,
        strategy: str | None = None,
        kind: str | None = None,
        since: float | None = None,
        until: float | None = None,
        run_id: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first, each with its latest outcome (``outcome``, ``outcome_detail``, ``client_order_id``).

        ``outcome`` filters on that latest outcome; ``"none"`` selects signals nothing was done with.
        """
        where, params = _filters(
            {
                "s.symbol=?": symbol,
                "s.strategy=?": strategy,
                "s.kind=?": kind,
                "s.run_id=?": run_id,
                "o.kind=?": None if outcome == "none" else outcome,
            },
            {"s.ts>=?": since, "s.ts<=?": until},
            extra="o.kind IS NULL" if outcome == "none" else None,
        )
        return self._all(
            "SELECT s.*, o.kind AS outcome, o.detail AS outcome_detail, o.client_order_id AS client_order_id "
            "FROM signals s LEFT JOIN signal_outcomes o ON o.id = "
            "(SELECT MAX(id) FROM signal_outcomes WHERE signal_id = s.id) "
            f"{where} ORDER BY s.ts DESC, s.rowid DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

    def signal(self, signal_id: str) -> dict[str, Any] | None:
        rows = self._all(
            "SELECT s.*, o.kind AS outcome, o.detail AS outcome_detail, o.client_order_id AS client_order_id "
            "FROM signals s LEFT JOIN signal_outcomes o ON o.id = "
            "(SELECT MAX(id) FROM signal_outcomes WHERE signal_id = s.id) WHERE s.id=?",
            (signal_id,),
        )
        return rows[0] if rows else None

    def signal_facets(self) -> dict[str, list[str]]:
        """Distinct strategies and symbols that ever signalled: the choices for filter menus."""
        with self._lock:
            return {
                "strategies": [r[0] for r in self._db.execute("SELECT DISTINCT strategy FROM signals ORDER BY 1")],
                "symbols": [r[0] for r in self._db.execute("SELECT DISTINCT symbol FROM signals ORDER BY 1")],
            }

    def outcomes(self, signal_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM signal_outcomes WHERE signal_id=? ORDER BY id", (signal_id,))

    def orders(
        self,
        limit: int = 100,
        offset: int = 0,
        symbol: str | None = None,
        status: str | None = None,
        since: float | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _filters({"symbol=?": symbol, "status=?": status, "run_id=?": run_id}, {"ts>=?": since})
        return self._all(
            f"SELECT * FROM orders {where} ORDER BY ts DESC, rowid DESC LIMIT ? OFFSET ?", (*params, limit, offset)
        )

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM orders WHERE client_order_id=? ORDER BY ts DESC LIMIT 1", (client_order_id,))

    def order_updates(self, source: str, order_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM order_updates WHERE source=? AND order_id=? ORDER BY id", (source, order_id))

    def fills(self, symbol: str | None = None, since: float | None = None) -> list[dict[str, Any]]:
        """Orders with a filled quantity, oldest first: the input for P&L and round-trip analytics.

        Each row also carries the causing signal's time, strategy and kind (None for manual orders).
        """
        where, params = _filters({"o.symbol=?": symbol}, {"o.ts>=?": since}, extra="o.filled_qty > 0")
        return self._all(
            "SELECT o.*, s.ts AS signal_ts, s.strategy AS strategy, s.kind AS signal_kind FROM orders o "
            f"LEFT JOIN signals s ON s.id = o.signal_id {where} ORDER BY o.ts, o.rowid",
            params,
        )

    def bars(
        self,
        symbol: str,
        timeframe: str,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """The newest ``limit`` bars in the range, returned oldest first."""
        where, params = _filters({"symbol=?": symbol, "timeframe=?": timeframe}, {"start>=?": since, "start<=?": until})
        rows = self._all(f"SELECT * FROM bars {where} ORDER BY start DESC LIMIT ?", (*params, limit))
        return rows[::-1]

    def equity(self, since: float | None = None, limit: int = 5000, run_id: str | None = None) -> list[dict[str, Any]]:
        """The newest ``limit`` snapshots that carry an equity value, oldest first (without positions)."""
        where, params = _filters({"run_id=?": run_id}, {"ts>=?": since}, extra="equity IS NOT NULL")
        rows = self._all(
            f"SELECT ts, run_id, equity, cash, buying_power, currency FROM equity_snapshots {where} "
            "ORDER BY ts DESC, id DESC LIMIT ?",
            (*params, limit),
        )
        return rows[::-1]

    def equity_peak(self, run_id: str | None = None) -> float | None:
        where, params = _filters({"run_id=?": run_id})
        row = self._one(f"SELECT MAX(equity) AS peak FROM equity_snapshots {where}", params)
        return row["peak"] if row else None

    def equity_first(self, since: float) -> float | None:
        """The first equity value recorded at or after ``since``."""
        row = self._one(
            "SELECT equity FROM equity_snapshots WHERE ts>=? AND equity IS NOT NULL ORDER BY ts, id LIMIT 1", (since,)
        )
        return row["equity"] if row else None

    def last_snapshot(self) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT 1")
        if row:
            row["positions"] = json.loads(row["positions"])
        return row

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    def prune(self, equity_days: float = 90.0, bar_days: float = 365.0) -> dict[str, int]:
        """Delete old high-volume rows: equity snapshots and intraday bars. Daily bars, signals,
        orders and the audit log are small and kept for good. Returns rows deleted per table."""
        now = to_epoch(utcnow())
        with self._lock, self._db:
            eq = self._db.execute("DELETE FROM equity_snapshots WHERE ts < ?", (now - equity_days * 86400,)).rowcount
            bars = self._db.execute(
                "DELETE FROM bars WHERE start < ? AND timeframe != ?", (now - bar_days * 86400, Timeframe.D1.value)
            ).rowcount
        return {"equity_snapshots": eq, "bars": bars}

    def size_bytes(self) -> int | None:
        """Size of the database file; None for an in-memory database."""
        if self.path == ":memory:":
            return None
        try:
            return sum(
                Path(self.path + suffix).stat().st_size for suffix in ("", "-wal") if Path(self.path + suffix).exists()
            )
        except OSError:
            return None

    def counts(self) -> dict[str, int]:
        tables = (
            "runs",
            "signals",
            "signal_outcomes",
            "orders",
            "order_updates",
            "bars",
            "equity_snapshots",
            "audit_log",
        )
        with self._lock:
            return {t: self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def _filters(
    equals: dict[str, Any], ranges: dict[str, Any] | None = None, extra: str | None = None
) -> tuple[str, list[Any]]:
    clauses, params = ([extra] if extra else []), []
    for clause, value in {**equals, **(ranges or {})}.items():
        if value is not None and value != "":
            clauses.append(clause)
            params.append(value)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


class StoreWriter:
    """Drains the bus into the store in batches, off the event loop."""

    def __init__(self, store: Store, bus: EventBus, run_id: str, batch: int = 500, queue_size: int = 20_000):
        self.store = store
        self.run_id = run_id
        self._batch = batch
        self._sub = bus.subscribe(
            maxsize=queue_size,
            types=(SignalEvent, SignalOutcome, OrderEvent, BarEvent, SnapshotEvent),
            name="store",
        )
        self._task: asyncio.Task | None = None
        self.failed_batches = 0
        #: called on the event loop with each batch once it is in the database
        self.listeners: list[Callable[[Sequence[Event]], None]] = []

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="store-writer")

    async def stop(self) -> None:
        """Stop the task and write whatever is still queued."""
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._sub.close()
        while True:
            batch = self._take()
            if not batch:
                break
            await self._write(batch)

    def _take(self) -> list[Event]:
        batch: list[Event] = []
        while len(batch) < self._batch:
            ev = self._sub.get_nowait()
            if ev is None:
                break
            batch.append(ev)
        return batch

    async def _run(self) -> None:
        while True:
            first = await self._sub.get()
            await self._write([first, *self._take()])

    async def _write(self, batch: list[Event]) -> None:
        try:
            # shield: a cancelled writer must not abandon a transaction half-way through a batch
            await asyncio.shield(asyncio.to_thread(self.store.apply, self.run_id, batch))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - persistence must never take the engine down
            self.failed_batches += 1
            log.exception("could not persist %d events; they are lost", len(batch))
            return
        for listener in self.listeners:
            try:
                listener(batch)
            except Exception:  # noqa: BLE001
                log.exception("store listener failed")
