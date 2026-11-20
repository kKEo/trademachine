"""FastAPI application factory for the admin console."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .security import ConsoleGuard
from .service import ConsoleService

HERE = Path(__file__).parent
DASH = "—"


def money(v: Any, digits: int = 2) -> str:
    return DASH if v is None else f"{v:,.{digits}f}"


def signed(v: Any, digits: int = 2) -> str:
    if v is None:
        return DASH
    if round(v, digits) == 0:
        return f"{0:.{digits}f}"  # never "-0.00"
    return f"{v:+,.{digits}f}".replace("-", "−")


def pct(v: Any, digits: int = 2) -> str:
    return DASH if v is None else signed(v, digits) + "%"


def qty(v: Any) -> str:
    return DASH if v is None else f"{v:,.4f}".rstrip("0").rstrip(".")


def tone(v: Any) -> str:
    """CSS class for a signed number."""
    if v is None or abs(v) < 1e-9:
        return ""
    return "up" if v > 0 else "down"


def clock(ts: Any) -> str:
    """Epoch seconds -> 'HH:MM:SS' today, 'MMM DD HH:MM' on other days (UTC)."""
    if ts is None:
        return DASH
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt.date() == datetime.now(timezone.utc).date():
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%b %d %H:%M")


def stamp(ts: Any) -> str:
    return "" if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def duration(sec: Any) -> str:
    if sec is None:
        return DASH
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m"
    return f"{sec // 86400}d {sec % 86400 // 3600}h"


def filesize(n: Any) -> str:
    if n is None:
        return "in memory"
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return DASH


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(
        money=money,
        signed=signed,
        pct=pct,
        qty=qty,
        tone=tone,
        clock=clock,
        stamp=stamp,
        duration=duration,
        filesize=filesize,
    )
    return templates


def create_app(service: ConsoleService, token: str | None = None) -> FastAPI:
    from . import api, views

    app = FastAPI(title="tradesignals console", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.token = token or None
    app.state.templates = make_templates()
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(api.router)
    app.include_router(views.router)
    app.add_middleware(ConsoleGuard, token=token)
    return app
