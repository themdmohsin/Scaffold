"""ASGI middleware: request context + access logging, and basic rate limiting.

Order matters. In app/main.py the middleware are added so the request flows:

    CORS → RequestContext → RateLimit → routes

CORS is outermost so even a 429 carries CORS headers (a browser must be able to
read the error). RequestContext wraps RateLimit so denied requests are still
logged with their request id.

Bodies and header values are NEVER logged. The access line is method, path
(no query string), status, duration, client IP and request id only.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.logging_setup import request_id_var
from app.services import rate_limit

access_logger = logging.getLogger("scaffold.access")
rate_logger = logging.getLogger("scaffold.ratelimit")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
# Probes hit these every few seconds; keep them out of the INFO access stream.
_QUIET_PATHS = frozenset({"/health", "/ready"})


def client_ip(scope: Scope) -> str:
    """Client IP for logs and rate-limit keys.

    X-Forwarded-For is honored only when SCAFFOLD_TRUST_PROXY is enabled —
    otherwise a client could spoof the header and dodge the limiter.
    """
    if settings.scaffold_trust_proxy:
        forwarded = Headers(scope=scope).get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    client = scope.get("client")
    if client:
        return str(client[0])
    return "unknown"


class RequestContextMiddleware:
    """Assign/propagate X-Request-Id and emit one structured access log per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                MutableHeaders(scope=message).setdefault("X-Request-Id", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope.get("path", "/")
            log = access_logger.debug if path in _QUIET_PATHS else access_logger.info
            log(
                "request",
                extra={
                    "http_method": scope.get("method", "-"),
                    "path": path,
                    "status": status_holder["status"],
                    "duration_ms": duration_ms,
                    "client": client_ip(scope),
                },
            )
            request_id_var.reset(token)


class RateLimitMiddleware:
    """429 for auth / reason / secret scopes once the per-minute budget is spent."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.scaffold_rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        scope_name = rate_limit.classify(method, path)
        if scope_name:
            limit = rate_limit.limit_for(scope_name, settings)
            key = rate_limit.key_for(
                scope_name, client_ip(scope), Headers(scope=scope).get("authorization")
            )
            decision = rate_limit.limiter.check(key, limit)
            if not decision.allowed:
                rate_logger.warning(
                    "rate_limit_exceeded",
                    extra={"scope": scope_name, "path": path, "limit": limit, "client": client_ip(scope)},
                )
                response = JSONResponse(
                    {
                        "detail": (
                            f"rate limit exceeded for {scope_name} endpoints — "
                            f"retry in {decision.retry_after_seconds}s"
                        )
                    },
                    status_code=429,
                    headers={
                        "Retry-After": str(decision.retry_after_seconds),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
