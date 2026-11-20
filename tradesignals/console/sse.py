"""Server-sent events: one long-lived response per browser tab."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from starlette.responses import StreamingResponse

from .service import ConsoleService

KEEPALIVE_SEC = 15.0


def encode(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream(service: ConsoleService, keepalive: float = KEEPALIVE_SEC) -> AsyncIterator[str]:
    q = service.connect()
    try:
        yield encode("hello", {"run_id": service.run_id})
        while not service.closing.is_set():
            try:
                n = await asyncio.wait_for(q.get(), timeout=keepalive)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield encode(n.name, n.data)
            if n.name == "bye":
                return
    finally:
        service.disconnect(q)


def response(service: ConsoleService) -> StreamingResponse:
    return StreamingResponse(
        stream(service),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
