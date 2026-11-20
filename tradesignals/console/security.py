"""Console access rules: loopback only, same-origin only, token for anything that changes state.

- The server binds to 127.0.0.1, and every request must also carry a loopback ``Host`` header.
  That second check defeats DNS rebinding, where a hostile web page reaches a local server
  under its own host name.
- State-changing requests (anything but GET/HEAD/OPTIONS) must come from the console's own
  origin and must present the console token. Without a configured token the console is
  read-only: there is no way to send an order from a browser by accident.
"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
#: Sent with every response. Scripts and styles only from the console itself; nothing may frame it.
SECURITY_HEADERS = [
    (
        b"content-security-policy",
        b"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]
TOKEN_COOKIE = "console_token"
TOKEN_HEADER = "x-console-token"


def host_name(value: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '[::1]'."""
    value = value.strip().lower()
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.rsplit(":", 1)[0] if ":" in value else value


def is_loopback(value: str) -> bool:
    return host_name(value) in LOOPBACK_HOSTS


def token_ok(expected: str | None, presented: str | None) -> bool:
    if not expected or not presented:
        return False
    return hmac.compare_digest(expected.encode(), presented.encode())


def presented_token(request: Request) -> str | None:
    return request.headers.get(TOKEN_HEADER) or request.cookies.get(TOKEN_COOKIE)


class ConsoleGuard:
    """Pure ASGI middleware (so that streaming responses are not buffered)."""

    def __init__(self, app, token: str | None):
        self.app = app
        self.token = token or None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        dynamic = not scope["path"].startswith("/static/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", [])) + SECURITY_HEADERS
                if dynamic:  # account data must not linger in a browser or proxy cache
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        refusal = self.check(request)
        if refusal is not None:
            await refusal(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)

    def check(self, request: Request) -> Response | None:
        if not is_loopback(request.headers.get("host", "")):
            return PlainTextResponse("The console only answers on localhost.", status_code=403)
        if request.method in SAFE_METHODS:
            return None
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
            return JSONResponse({"error": "Cross-origin requests are refused."}, status_code=403)
        if request.url.path == "/unlock":  # the one POST that presents the token in its body
            return None
        if self.token is None:
            return JSONResponse(
                {"error": "The console is read-only. Set CONSOLE_TOKEN and restart to enable actions."},
                status_code=403,
            )
        if not token_ok(self.token, presented_token(request)):
            return JSONResponse({"error": "Unlock the console with its token first."}, status_code=401)
        return None
