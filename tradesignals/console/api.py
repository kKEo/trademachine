"""JSON API: what the page scripts and the terminal UI read."""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from ..models import Timeframe
from . import sse
from .security import TOKEN_COOKIE, token_ok
from .service import ActionError, ConsoleService, strategy_catalog

router = APIRouter()

MAX_PERIOD = 500


def parse_timeframe(value: str | None, default: Timeframe) -> Timeframe:
    if not value:
        return default
    try:
        return Timeframe(value)
    except ValueError:
        choices = ", ".join(t.value for t in Timeframe)
        raise HTTPException(422, f"Unknown timeframe '{value}'. Use one of: {choices}.") from None


def parse_periods(value: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    """'10,30' -> (10, 30); empty string -> no series; None -> the default."""
    if value is None:
        return default
    try:
        periods = tuple(int(p) for p in value.split(",") if p.strip())
    except ValueError:
        raise HTTPException(422, "Indicator periods must be whole numbers, for example 10,30.") from None
    if len(periods) > 4 or any(not 1 < p <= MAX_PERIOD for p in periods):
        raise HTTPException(422, f"Use at most four periods between 2 and {MAX_PERIOD}.")
    return periods


def known_symbol(service: ConsoleService, symbol: str) -> str:
    symbol = symbol.upper()
    if symbol not in service.known_symbols():
        raise HTTPException(404, f"{symbol} is not traded or held in this session.")
    return symbol


def svc(request: Request) -> ConsoleService:
    return request.app.state.service


@router.get("/events")
async def events(request: Request):
    return sse.response(svc(request))


@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    return svc(request).status()


@router.get("/api/overview")
async def overview(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {"status": s.status(), "kpis": await s.kpis(), "positions": s.positions(), "quotes": s.quotes()}


@router.get("/api/quotes")
async def quotes(request: Request) -> list[dict[str, Any]]:
    return svc(request).quotes()


@router.get("/api/positions")
async def positions(request: Request) -> list[dict[str, Any]]:
    return svc(request).positions()


@router.get("/api/signals")
async def signals(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    symbol: str | None = None,
    strategy: str | None = None,
    kind: str | None = None,
    since: float | None = None,
    until: float | None = None,
) -> list[dict[str, Any]]:
    return await svc(request).signals(
        limit=limit, offset=offset, symbol=symbol, strategy=strategy, kind=kind, since=since, until=until
    )


@router.get("/api/orders")
async def orders(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    symbol: str | None = None,
    status: str | None = None,
    since: float | None = None,
) -> list[dict[str, Any]]:
    return await svc(request).orders(limit=limit, offset=offset, symbol=symbol, status=status, since=since)


@router.get("/api/equity")
async def equity(request: Request, since: float | None = None) -> list[dict[str, Any]]:
    return await svc(request).equity(since=since)


@router.get("/api/markets")
async def markets(request: Request) -> list[dict[str, Any]]:
    return await svc(request).watchlist()


@router.get("/api/bars/{symbol}")
async def bars(
    request: Request,
    symbol: str,
    tf: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    before: float | None = None,
    sma: str | None = None,
    ema: str | None = None,
    rsi: str | None = None,
) -> dict[str, Any]:
    s = svc(request)
    symbol = known_symbol(s, symbol)
    return await s.chart(
        symbol,
        parse_timeframe(tf, s.timeframe_for(symbol)),
        limit=limit,
        before=before,
        sma=parse_periods(sma, (10, 30)),
        ema=parse_periods(ema, (20,)),
        rsi=parse_periods(rsi, (14,)),
    )


# ---------------------------------------------------------------------- signal analysis
OUTCOMES = ("order", "no_op", "risk_reject", "submit_failed", "none")
KINDS = ("long", "short", "flat")


def parse_day(value: str | None, end: bool = False) -> float | None:
    """'2026-11-20' -> epoch seconds of that UTC day's start (or its last second with ``end``)."""
    if not value:
        return None
    try:
        day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, "Dates must look like 2026-11-20.") from None
    return day.timestamp() + (86399.999 if end else 0.0)


def signal_filters(
    service: ConsoleService,
    strategy: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    outcome: str | None = None,
    day_from: str | None = None,
    day_to: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Validated keyword arguments for the store's signal query, from query-string values."""
    if kind and kind not in KINDS:
        raise HTTPException(422, f"Unknown signal kind '{kind}'.")
    if outcome and outcome not in OUTCOMES:
        raise HTTPException(422, f"Unknown result '{outcome}'.")
    return {
        "strategy": strategy or None,
        "symbol": symbol.upper() if symbol else None,
        "kind": kind or None,
        "outcome": outcome or None,
        "since": parse_day(day_from),
        "until": parse_day(day_to, end=True),
        "run_id": service.run_id if scope == "run" and service.run_id else None,
    }


@router.get("/api/signals/report")
async def signal_report(
    request: Request,
    strategy: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    outcome: str | None = None,
    day_from: str | None = None,
    day_to: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    s = svc(request)
    return await s.signal_report(**signal_filters(s, strategy, symbol, kind, outcome, day_from, day_to, scope))


@router.get("/api/signals/{signal_id}")
async def signal_detail(request: Request, signal_id: str) -> dict[str, Any]:
    detail = await svc(request).signal_detail(signal_id)
    if detail is None:
        raise HTTPException(404, "No such signal.")
    return detail


@router.get("/api/trades")
async def trades(request: Request) -> dict[str, Any]:
    return await svc(request).trade_report()


@router.get("/signals.csv")
async def signals_csv(
    request: Request,
    strategy: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    outcome: str | None = None,
    day_from: str | None = None,
    day_to: str | None = None,
    scope: str | None = None,
) -> Response:
    s = svc(request)
    rows = await s.analysed_signals(
        limit=100_000, **signal_filters(s, strategy, symbol, kind, outcome, day_from, day_to, scope)
    )
    out = io.StringIO()
    w = csv.writer(out)
    horizons = [1, 5, 15]
    w.writerow(
        ["time_utc", "symbol", "signal", "strategy", "timeframe", "strength", "reason", "result", "result_detail"]
        + ["price_at_signal"]
        + [f"move_pct_after_{h}_bars" for h in horizons]
    )
    for r in rows:
        w.writerow(
            [
                datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                r["symbol"],
                r["kind"],
                r["strategy"],
                r["timeframe"],
                r["strength"],
                r["reason"],
                r.get("outcome") or "none",
                r.get("outcome_detail") or "",
                "" if r["ref"] is None else r["ref"],
            ]
            + ["" if r["returns"].get(h) is None else round(r["returns"][h], 4) for h in horizons]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="signals.csv"'},
    )


# ---------------------------------------------------------------------- access and actions
# The ConsoleGuard middleware has already checked the Host, the Origin and (except for /unlock)
# the token before any of these run.
async def body_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/unlock")
async def unlock(request: Request) -> JSONResponse:
    expected = request.app.state.token
    if expected is None:
        return JSONResponse(
            {"ok": False, "error": "The console is read-only. Set CONSOLE_TOKEN and restart to enable actions."},
            status_code=403,
        )
    presented = str((await body_json(request)).get("token") or "")
    if not token_ok(expected, presented):
        await asyncio.sleep(0.5)  # slow down guessing
        return JSONResponse({"ok": False, "error": "That is not the console token."}, status_code=401)
    response = JSONResponse({"ok": True, "message": "Actions unlocked in this browser."})
    response.set_cookie(TOKEN_COOKIE, presented, httponly=True, samesite="strict", path="/")
    return response


@router.post("/lock")
async def lock() -> JSONResponse:
    response = JSONResponse({"ok": True, "message": "Actions locked."})
    response.delete_cookie(TOKEN_COOKIE, path="/")
    return response


async def run_action(coro) -> JSONResponse:
    try:
        return JSONResponse(await coro)
    except ActionError as e:
        return JSONResponse({"ok": False, "error": e.message}, status_code=e.status)


@router.post("/api/actions/halt")
async def action_halt(request: Request) -> JSONResponse:
    return await run_action(svc(request).halt(str((await body_json(request)).get("reason") or "")))


@router.post("/api/actions/resume")
async def action_resume(request: Request) -> JSONResponse:
    return await run_action(svc(request).resume())


@router.post("/api/actions/flatten")
async def action_flatten(request: Request) -> JSONResponse:
    data = await body_json(request)
    symbol = str(data.get("symbol") or "") or None
    return await run_action(svc(request).flatten(symbol, str(data.get("confirm") or "")))


@router.post("/api/actions/cancel")
async def action_cancel(request: Request) -> JSONResponse:
    return await run_action(svc(request).cancel(str((await body_json(request)).get("order_id") or "")))


@router.post("/api/actions/kill-switch")
async def action_kill(request: Request) -> JSONResponse:
    return await run_action(svc(request).kill_switch(str((await body_json(request)).get("confirm") or "")))


@router.post("/api/actions/order/preview")
async def action_order_preview(request: Request) -> JSONResponse:
    return await run_action(svc(request).preview_order(await body_json(request)))


@router.post("/api/actions/order")
async def action_order(request: Request) -> JSONResponse:
    return await run_action(svc(request).send_order(await body_json(request)))


@router.post("/api/actions/limits")
async def action_limits(request: Request) -> JSONResponse:
    return await run_action(svc(request).set_limits(await body_json(request)))


@router.post("/api/actions/strategies/add")
async def action_strategy_add(request: Request) -> JSONResponse:
    return await run_action(svc(request).add_strategy(await body_json(request)))


@router.post("/api/actions/strategies/{action}")
async def action_strategy(request: Request, action: str) -> JSONResponse:
    sid = str((await body_json(request)).get("id") or "")
    return await run_action(svc(request).strategy_action(action, sid))


@router.get("/api/strategies")
async def strategies(request: Request) -> dict[str, Any]:
    return {"running": svc(request).strategies_view(), "catalog": strategy_catalog()}


@router.get("/api/risk")
async def risk(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {"risk": s.risk_view(), "capabilities": s.capabilities(), "open_orders": s.open_orders()}


@router.get("/api/audit")
async def audit(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return await svc(request).audit_log(limit)


@router.get("/api/system")
async def system(request: Request) -> dict[str, Any]:
    s = svc(request)
    return {"health": await s.health(), "settings": s.settings_view(), "runs": await s.runs()}


@router.get("/api/logs")
async def logs(
    request: Request, limit: int = Query(200, ge=1, le=500), level: str | None = None
) -> list[dict[str, Any]]:
    return svc(request).log_tail(limit, level)
