"""Security middleware: response headers, request size limits and rate limiting (NFR-06, NFR-08).

The API is served under /api on the app's own origin, so cross-origin requests are refused
outright unless an origin is configured. Every control here is enforced before a route runs.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger("firebid.security")

# The API serves JSON, never a document a browser should render, script or frame. These stay
# useful even so: a stray HTML error page cannot be sniffed, framed, or leak a full URL.
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
    # Told to browsers only over HTTPS; harmless on plain HTTP, which ignores it.
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
)


class SecurityHeadersMiddleware:
    """Adds the standard headers to every response, without overwriting a route's own."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers.setdefault(name.decode(), value.decode())
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def _refuse(send: Send, status: int, detail: str) -> None:
    body = f'{{"detail":"{detail}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *SECURITY_HEADERS,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Refuses a body larger than the limit, by its declared length and as it arrives.

    Uploads have their own, larger limit. They are received to disk and then read into
    memory a file at a time, so that limit is also the most one request can hold.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, upload_max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes

    def _limit_for(self, scope: Scope) -> int:
        path = scope.get("path", "")
        return self.upload_max_bytes if "/documents" in path else self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            log.warning("body_too_large", path=scope.get("path"), declared=int(declared))
            await _refuse(send, 413, "request body too large")
            return

        received = 0
        refused = False

        async def counting_receive() -> Message:
            nonlocal received, refused
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Answer from here: an exception raised in the receive channel is caught
                    # upstream and reported as a malformed body, which this is not.
                    log.warning("body_too_large", path=scope.get("path"), received=received)
                    await _refuse(send, 413, "request body too large")
                    refused = True
                    return {"type": "http.disconnect"}
            return message

        async def send_unless_refused(message: Message) -> None:
            if not refused:
                await send(message)

        await self.app(scope, counting_receive, send_unless_refused)


@dataclass
class _Window:
    """A fixed window per caller, holding only the timestamps still inside it."""

    hits: deque[float] = field(default_factory=deque)


class RateLimitMiddleware:
    """A per-caller request budget, to blunt credential stuffing and runaway clients.

    In-process, which is right while the API runs as one service; a shared limiter moves to
    Redis or the ingress when it does not. Callers are identified by their bearer token, not
    by IP, so one office behind one address is not one caller.
    """

    def __init__(
        self, app: ASGIApp, requests_per_minute: int, exempt_paths: tuple[str, ...] = ()
    ) -> None:
        self.app = app
        self.requests_per_minute = requests_per_minute
        self.exempt_paths = exempt_paths
        self._windows: dict[str, _Window] = defaultdict(_Window)

    def _caller(self, scope: Scope) -> str:
        token = Headers(scope=scope).get("authorization", "")
        if token:
            # The token itself is never logged or stored; only a bucket key derived from it.
            return f"token:{hash(token)}"
        client = scope.get("client")
        return f"ip:{client[0] if client else 'unknown'}"

    def _allowed(self, caller: str, now: float) -> bool:
        window = self._windows[caller]
        cutoff = now - 60.0
        while window.hits and window.hits[0] < cutoff:
            window.hits.popleft()
        if len(window.hits) >= self.requests_per_minute:
            return False
        window.hits.append(now)
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "") in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        if not self._allowed(self._caller(scope), time.monotonic()):
            log.warning("rate_limited", path=scope.get("path"))
            await _refuse(send, 429, "too many requests; slow down")
            return
        await self.app(scope, receive, send)
