"""Pure analysis functions for the console: no I/O, no engine, no provider knowledge.

Inputs are canonical ``Bar`` objects or plain rows (dicts) as the store returns them; outputs
are plain data. Indicator series are produced by the same incremental indicators the strategies
use, so a line on a chart is exactly what a strategy saw.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .bars import BarAggregator
from .indicators import EMA, RSI, SMA
from .models import Bar, Timeframe

Point = dict[str, float]  # {"time": epoch seconds, "value": number}

RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0


# ---------------------------------------------------------------------- bars
def resample(bars: Sequence[Bar], timeframe: Timeframe) -> list[Bar]:
    """Roll finer bars up into ``timeframe`` buckets. The last bucket may still be in progress."""
    if not bars:
        return []
    if all(b.timeframe == timeframe for b in bars):
        return list(bars)
    agg = BarAggregator(bars[0].symbol, timeframe)
    out = [done for b in bars if (done := agg.on_bar(b)) is not None]
    if agg.current is not None:
        out.append(agg.current)
    return out


def merge_bars(*series: Iterable[Bar]) -> list[Bar]:
    """Union by bar start, later series winning, oldest first."""
    by_start: dict[float, Bar] = {}
    for bars in series:
        for b in bars:
            by_start[b.start.timestamp()] = b
    return [by_start[k] for k in sorted(by_start)]


# ---------------------------------------------------------------------- indicators
def _series(bars: Sequence[Bar], indicator: SMA | EMA | RSI, skip: int = 0) -> list[Point]:
    out: list[Point] = []
    for i, b in enumerate(bars):
        v = indicator.update(b.close)
        if v is not None and i >= skip:
            out.append({"time": int(b.start.timestamp()), "value": v})
    return out


def sma_series(bars: Sequence[Bar], n: int) -> list[Point]:
    return _series(bars, SMA(n))


def ema_series(bars: Sequence[Bar], n: int) -> list[Point]:
    # An EMA has a value from the first bar on, but it only means something after ~n bars.
    return _series(bars, EMA(n), skip=n - 1)


def rsi_series(bars: Sequence[Bar], n: int = 14) -> list[Point]:
    return _series(bars, RSI(n))


def indicator_series(
    bars: Sequence[Bar], sma: Iterable[int] = (), ema: Iterable[int] = (), rsi: Iterable[int] = ()
) -> dict[str, list[Point]]:
    out: dict[str, list[Point]] = {}
    for n in sma:
        out[f"sma{n}"] = sma_series(bars, n)
    for n in ema:
        out[f"ema{n}"] = ema_series(bars, n)
    for n in rsi:
        out[f"rsi{n}"] = rsi_series(bars, n)
    return out


def trend_state(bars: Sequence[Bar], fast: int = 10, slow: int = 30, rsi_n: int = 14) -> dict[str, Any]:
    """Where a symbol stands right now: fast vs slow average, RSI zone, change over the window."""
    closes = [b.close for b in bars]
    f, s, r = SMA(fast), SMA(slow), RSI(rsi_n)
    fv = sv = rv = None
    for c in closes:
        fv, sv, rv = f.update(c), s.update(c), r.update(c)
    trend = None
    if fv is not None and sv is not None:
        trend = "up" if fv > sv else "down" if fv < sv else "flat"
    zone = None
    if rv is not None:
        zone = "oversold" if rv < RSI_OVERSOLD else "overbought" if rv > RSI_OVERBOUGHT else "neutral"
    return {
        "bars": len(closes),
        "last": closes[-1] if closes else None,
        "change_pct": (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 and closes[0] else None,
        "sma_fast": fv,
        "sma_slow": sv,
        "trend": trend,
        "spread_pct": (fv / sv - 1) * 100 if fv is not None and sv else None,
        "rsi": rv,
        "rsi_zone": zone,
        "fast": fast,
        "slow": slow,
        "rsi_n": rsi_n,
    }


def sparkline(values: Sequence[float], width: float = 120.0, height: float = 28.0, pad: float = 2.0) -> str:
    """SVG polyline points for a tiny line chart; empty string when there is nothing to draw."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(values) - 1)
    return " ".join(
        f"{pad + i * step:.1f},{height - pad - (v - lo) / span * (height - 2 * pad):.1f}" for i, v in enumerate(values)
    )


# ---------------------------------------------------------------------- trades
EPS = 1e-9


def round_trips(fills: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn fills into round trips: from flat, through any adds and partial exits, back to flat.

    ``fills`` are order rows (``symbol``, ``side``, ``filled_qty``, ``avg_fill_price``, ``ts`` and
    optionally ``signal_ts`` / ``strategy`` / ``origin``), oldest first. Fills without a price are
    skipped. A fill that takes a position through zero closes one trip and opens the next. The
    last trip of a symbol may still be open (``open`` is true, ``exit_*`` are None).
    Trips are returned oldest first by entry time.
    """
    state: dict[str, dict[str, Any]] = {}
    trips: list[dict[str, Any]] = []

    def start(f: dict[str, Any], when: float, direction: int) -> dict[str, Any]:
        trip = {
            "symbol": f["symbol"],
            "direction": "long" if direction > 0 else "short",
            "strategy": f.get("strategy") or (f.get("origin") or "").split(":")[0] or "manual",
            "entry_ts": when,
            "exit_ts": None,
            "qty": 0.0,  # largest size the position reached
            "entry_qty": 0.0,
            "entry_value": 0.0,
            "exit_qty": 0.0,
            "exit_value": 0.0,
            "pnl": 0.0,
            "open": True,
            "fills": 0,
        }
        trips.append(trip)
        return trip

    for f in fills:
        price, qty = f.get("avg_fill_price"), f.get("filled_qty") or 0.0
        if price is None or qty <= EPS:
            continue
        when = f.get("signal_ts") or f["ts"]
        st = state.setdefault(f["symbol"], {"pos": 0.0, "avg": 0.0, "trip": None})
        remaining = qty if f["side"] == "buy" else -qty
        while abs(remaining) > EPS:
            pos = st["pos"]
            if abs(pos) <= EPS or (pos > 0) == (remaining > 0):  # opening or adding
                if abs(pos) <= EPS:
                    st["trip"], st["pos"], st["avg"] = start(f, when, 1 if remaining > 0 else -1), 0.0, 0.0
                    pos = 0.0
                trip = st["trip"]
                st["avg"] = (st["avg"] * abs(pos) + price * abs(remaining)) / (abs(pos) + abs(remaining))
                st["pos"] = pos + remaining
                trip["entry_qty"] += abs(remaining)
                trip["entry_value"] += abs(remaining) * price
                trip["qty"] = max(trip["qty"], abs(st["pos"]))
                trip["fills"] += 1
                remaining = 0.0
            else:  # reducing, closing or flipping
                trip = st["trip"]
                closing = min(abs(remaining), abs(pos))
                direction = 1 if pos > 0 else -1
                trip["pnl"] += closing * (price - st["avg"]) * direction
                trip["exit_qty"] += closing
                trip["exit_value"] += closing * price
                trip["fills"] += 1
                st["pos"] = pos - direction * closing
                remaining += direction * closing
                if abs(st["pos"]) <= EPS:
                    st["pos"] = 0.0
                    trip["open"], trip["exit_ts"] = False, when
                    st["trip"] = None

    for t in trips:
        t["entry_price"] = t["entry_value"] / t["entry_qty"] if t["entry_qty"] else None
        t["exit_price"] = t["exit_value"] / t["exit_qty"] if t["exit_qty"] else None
        cost = (t["entry_price"] or 0.0) * t["exit_qty"]
        t["pnl_pct"] = t["pnl"] / cost * 100 if cost else None
        t["hold_sec"] = t["exit_ts"] - t["entry_ts"] if t["exit_ts"] is not None else None
        t["open_qty"] = t["entry_qty"] - t["exit_qty"]
    return sorted(trips, key=lambda t: t["entry_ts"])


def trip_stats(trips: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Win rate, profit factor and friends over the closed trips; realised P&L includes partial exits."""
    trips = list(trips)
    closed = [t for t in trips if not t["open"]]
    wins = [t["pnl"] for t in closed if t["pnl"] > EPS]
    losses = [t["pnl"] for t in closed if t["pnl"] < -EPS]
    holds = [t["hold_sec"] for t in closed if t["hold_sec"] is not None]
    return {
        "trades": len(closed),
        "open": len(trips) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) * 100 if closed else None,
        "pnl": sum(t["pnl"] for t in trips),
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "best": max((t["pnl"] for t in closed), default=None),
        "worst": min((t["pnl"] for t in closed), default=None),
        "avg_hold_sec": sum(holds) / len(holds) if holds else None,
    }


def group_stats(trips: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """``trip_stats`` per value of ``key`` ("symbol" or "strategy")."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in trips:
        groups.setdefault(t[key], []).append(t)
    return {k: trip_stats(v) for k, v in sorted(groups.items())}


def max_drawdown(values: Sequence[float]) -> dict[str, float | None]:
    """Deepest and current fall from the running peak, in percent (zero or negative)."""
    peak, worst, current = None, 0.0, None
    for v in values:
        peak = v if peak is None else max(peak, v)
        current = (v / peak - 1) * 100 if peak else 0.0
        worst = min(worst, current)
    return {"max_pct": worst if values else None, "current_pct": current}


# ---------------------------------------------------------------------- signals
HORIZONS = (1, 5, 15)
DIRECTION = {"long": 1.0, "short": -1.0, "flat": -1.0}  # leaving before a fall is a good exit


def forward_returns(
    kind: str, ts: float, bar_times: Sequence[float], closes: Sequence[float], horizons: Sequence[int] = HORIZONS
) -> dict[str, Any]:
    """What the price did after a signal.

    The reference price is the close of the bar the signal fired on (the last bar starting at or
    before ``ts``). ``returns[h]`` is the raw move in percent after ``h`` further bars; ``edge[h]``
    is that move seen from the signal's side: positive when a LONG was followed by a rise, or a
    FLAT/SHORT by a fall. Horizons beyond the available bars are None.
    """
    from bisect import bisect_right

    i = bisect_right(bar_times, ts) - 1
    if i < 0 or not closes[i]:
        return {"ref": None, "returns": {h: None for h in horizons}, "edge": {h: None for h in horizons}}
    ref = closes[i]
    sign = DIRECTION.get(kind, 1.0)
    returns = {h: (closes[i + h] / ref - 1) * 100 if i + h < len(closes) else None for h in horizons}
    return {"ref": ref, "returns": returns, "edge": {h: None if r is None else r * sign for h, r in returns.items()}}


def signal_stats(signals: Iterable[dict[str, Any]], horizons: Sequence[int] = HORIZONS) -> dict[str, dict[str, Any]]:
    """Per strategy: how many signals, what happened to them, and whether the market agreed.

    Each signal row needs ``strategy``, ``kind``, ``outcome`` and, for the forward-looking part,
    ``edge`` as produced by ``forward_returns``. ``hit_rate[h]`` is the share of signals whose
    edge after ``h`` bars was positive; ``avg_edge[h]`` is their mean edge in percent.
    """
    out: dict[str, dict[str, Any]] = {}
    for s in signals:
        g = out.setdefault(
            s["strategy"],
            {
                "signals": 0,
                "kinds": {},
                "outcomes": {},
                "_edges": {h: [] for h in horizons},
            },
        )
        g["signals"] += 1
        g["kinds"][s["kind"]] = g["kinds"].get(s["kind"], 0) + 1
        outcome = s.get("outcome") or "none"
        g["outcomes"][outcome] = g["outcomes"].get(outcome, 0) + 1
        for h in horizons:
            e = (s.get("edge") or {}).get(h)
            if e is not None:
                g["_edges"][h].append(e)
    for g in out.values():
        edges = g.pop("_edges")
        g["measured"] = {h: len(v) for h, v in edges.items()}
        g["avg_edge"] = {h: sum(v) / len(v) if v else None for h, v in edges.items()}
        g["hit_rate"] = {h: sum(e > 0 for e in v) / len(v) * 100 if v else None for h, v in edges.items()}
    return dict(sorted(out.items()))


def execution_quality(
    side: str, fill_price: float | None, ref_price: float | None, signal_ts: float, order_ts: float, bar_seconds: int
) -> dict[str, float | None]:
    """How well a signal was executed.

    ``slippage_pct`` compares the fill with the close of the signal's bar; positive means worse
    (paid more on a buy, received less on a sell). ``latency_sec`` runs from the close of the
    signal's bar to the order; it is only reported when it is plausible for a live run (a replay
    stamps orders with today's clock, which says nothing about speed).
    """
    slippage = None
    if fill_price is not None and ref_price:
        slippage = (fill_price / ref_price - 1) * 100 * (1.0 if side == "buy" else -1.0)
    latency = order_ts - (signal_ts + bar_seconds)
    return {"slippage_pct": slippage, "latency_sec": latency if 0 <= latency <= 3600 else None}
