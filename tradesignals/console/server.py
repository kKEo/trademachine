"""Run the console's HTTP server on the engine's event loop.

The console is a guest in the engine process: if it cannot start (port in use) or dies, the
failure is logged and the engine keeps trading.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

log = logging.getLogger("tradesignals.console")

HOST = "127.0.0.1"  # never configurable: the console is local-only by design


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # The CLI owns SIGINT/SIGTERM (they stop the engine); uvicorn must not replace its handlers.
        yield

    def install_signal_handlers(self) -> None:  # older uvicorn releases
        return


class ConsoleServer:
    def __init__(self, app, port: int):
        self.port = port
        self._server = _Server(
            uvicorn.Config(
                app,
                host=HOST,
                port=port,
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=2,
                lifespan="off",
            )
        )
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def start(self) -> None:
        self._task = asyncio.create_task(self._serve(), name="console-server")

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - uvicorn exits with SystemExit when the port is taken
            log.error("console server stopped: %r (the engine keeps running)", e)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
