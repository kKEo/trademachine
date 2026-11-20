"""HTML pages and the partials that HTMX swaps in when something changes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ..models import Timeframe
from .api import known_symbol, parse_timeframe, signal_filters
from .security import presented_token, token_ok
from .service import CONFIRM_FLATTEN_ALL, CONFIRM_KILL, ConsoleService, strategy_catalog

router = APIRouter(default_response_class=HTMLResponse)

NAV = [
    ("overview", "/", "Overview"),
    ("portfolio", "/portfolio", "Portfolio"),
    ("markets", "/markets", "Markets"),
    ("signals", "/signals", "Signals"),
    ("orders", "/orders", "Orders"),
    ("strategies", "/strategies", "Strategies"),
    ("risk", "/risk", "Risk"),
    ("system", "/system", "System"),
]


def svc(request: Request) -> ConsoleService:
    return request.app.state.service


def render(request: Request, template: str, **context: Any) -> HTMLResponse:
    s = svc(request)
    token = request.app.state.token
    access = {
        "configured": token is not None,
        "unlocked": token_ok(token, presented_token(request)),
        **s.capabilities(),
    }
    # what the page may offer: actions need the token, trading actions also a paper account
    access["actions"] = access["configured"] and access["unlocked"]
    access["trading"] = access["actions"] and access["can_trade"]
    base = {
        "nav": NAV,
        "status": s.status(),
        "quotes": s.quotes(),
        "access": access,
        "confirm_flatten": CONFIRM_FLATTEN_ALL,
        "confirm_kill": CONFIRM_KILL,
    }
    return request.app.state.templates.TemplateResponse(request, template, {**base, **context})


# ---------------------------------------------------------------------- pages
@router.get("/")
async def overview(request: Request):
    s = svc(request)
    return render(
        request,
        "overview.html",
        page="overview",
        kpis=await s.kpis(),
        positions=s.positions(),
        signals=await s.signals(limit=12),
        orders=await s.orders(limit=8),
    )


@router.get("/portfolio")
async def portfolio(request: Request):
    s = svc(request)
    return render(
        request,
        "portfolio.html",
        page="portfolio",
        kpis=await s.kpis(),
        positions=s.positions(),
        open_orders=s.open_orders(),
        trades=await s.trade_report(),
        symbols=s.known_symbols(),
    )


@router.get("/risk")
async def risk(request: Request):
    s = svc(request)
    return render(
        request, "risk.html", page="risk", risk=s.risk_view(), limits=s.limits_view(), audit=await s.audit_log(30)
    )


@router.get("/strategies")
async def strategies(request: Request):
    s = svc(request)
    return render(
        request,
        "strategies.html",
        page="strategies",
        running=s.strategies_view(),
        catalog=strategy_catalog(),
        symbols=list(s.engine.symbols),
        timeframes=[t.value for t in Timeframe],
    )


@router.get("/partials/strategies")
async def partial_strategies(request: Request):
    return render(request, "partials/strategies.html", running=svc(request).strategies_view())


@router.get("/partials/risk")
async def partial_risk(request: Request):
    return render(request, "partials/risk.html", risk=svc(request).risk_view())


@router.get("/partials/audit")
async def partial_audit(request: Request):
    return render(request, "partials/audit.html", audit=await svc(request).audit_log(30))


@router.get("/partials/open-orders")
async def partial_open_orders(request: Request):
    return render(request, "partials/open_orders.html", open_orders=svc(request).open_orders())


@router.get("/partials/access")
async def partial_access(request: Request):
    return render(request, "partials/access.html")


@router.get("/orders")
async def orders(request: Request):
    return render(request, "orders.html", page="orders", orders=await svc(request).orders(limit=200))


@router.get("/markets")
async def markets(request: Request):
    return render(request, "markets.html", page="markets", rows=await svc(request).watchlist())


@router.get("/markets/{symbol}")
async def market(request: Request, symbol: str, tf: str | None = None):
    s = svc(request)
    symbol = known_symbol(s, symbol)
    timeframe = parse_timeframe(tf, s.timeframe_for(symbol))
    row = next((r for r in await s.watchlist() if r["symbol"] == symbol), None)
    return render(
        request,
        "market.html",
        page="markets",
        symbol=symbol,
        timeframe=timeframe.value,
        timeframes=[t.value for t in Timeframe],
        row=row,
        signals=await s.signals(symbol=symbol, limit=15),
    )


SIGNAL_PAGE_SIZE = 200


async def _signals_context(
    request: Request,
    strategy: str | None,
    symbol: str | None,
    kind: str | None,
    outcome: str | None,
    day_from: str | None,
    day_to: str | None,
    scope: str | None,
) -> dict[str, Any]:
    s = svc(request)
    report = await s.signal_report(**signal_filters(s, strategy, symbol, kind, outcome, day_from, day_to, scope))
    chosen = {
        "strategy": strategy or "",
        "symbol": (symbol or "").upper(),
        "kind": kind or "",
        "outcome": outcome or "",
        "day_from": day_from or "",
        "day_to": day_to or "",
        "scope": scope or "",
    }
    return {
        "report": report,
        "rows": report["signals"][:SIGNAL_PAGE_SIZE],
        "chosen": chosen,
        "query": str(request.url.query),
        "filtered": any(chosen.values()),
    }


@router.get("/signals")
async def signals(
    request: Request,
    strategy: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    outcome: str | None = None,
    day_from: str | None = None,
    day_to: str | None = None,
    scope: str | None = None,
):
    ctx = await _signals_context(request, strategy, symbol, kind, outcome, day_from, day_to, scope)
    return render(request, "signals.html", page="signals", **ctx)


@router.get("/partials/signal-report")
async def partial_signal_report(
    request: Request,
    strategy: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    outcome: str | None = None,
    day_from: str | None = None,
    day_to: str | None = None,
    scope: str | None = None,
):
    ctx = await _signals_context(request, strategy, symbol, kind, outcome, day_from, day_to, scope)
    return render(request, "partials/signal_report.html", **ctx)


@router.get("/signals/{signal_id}")
async def signal(request: Request, signal_id: str):
    detail = await svc(request).signal_detail(signal_id)
    if detail is None:
        raise HTTPException(404, "No such signal.")
    return render(request, "signal.html", page="signals", **detail)


@router.get("/partials/trades")
async def partial_trades(request: Request):
    return render(request, "partials/trades.html", trades=await svc(request).trade_report())


# ---------------------------------------------------------------------- partials
@router.get("/partials/status")
async def partial_status(request: Request):
    return render(request, "partials/status.html")


@router.get("/partials/kpis")
async def partial_kpis(request: Request):
    return render(request, "partials/kpis.html", kpis=await svc(request).kpis())


@router.get("/partials/positions")
async def partial_positions(request: Request, compact: bool = False):
    return render(request, "partials/positions.html", positions=svc(request).positions(), compact=compact)


@router.get("/partials/signals")
async def partial_signals(request: Request, limit: int = Query(12, ge=1, le=500)):
    return render(request, "partials/signals.html", signals=await svc(request).signals(limit=limit))


@router.get("/partials/orders")
async def partial_orders(request: Request, limit: int = Query(8, ge=1, le=500), compact: bool = False):
    return render(request, "partials/orders.html", orders=await svc(request).orders(limit=limit), compact=compact)


@router.get("/partials/watchlist")
async def partial_watchlist(request: Request):
    return render(request, "partials/watchlist.html", rows=await svc(request).watchlist())


@router.get("/system")
async def system(request: Request, level: str | None = None):
    s = svc(request)
    level = "WARNING" if level and level.upper() == "WARNING" else None
    return render(
        request,
        "system.html",
        page="system",
        health=await s.health(),
        settings=s.settings_view(),
        runs=await s.runs(),
        logs=s.log_tail(200, level),
        level=level,
        audit=await s.audit_log(30),
    )


@router.get("/partials/logs")
async def partial_logs(request: Request, level: str | None = None):
    return render(request, "partials/logs.html", logs=svc(request).log_tail(200, level))


@router.get("/partials/health")
async def partial_health(request: Request):
    return render(request, "partials/health.html", health=await svc(request).health())
