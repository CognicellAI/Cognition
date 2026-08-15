"""FastAPI middleware for observability."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import cast

from fastapi import Request, Response
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from server.app.observability import (
    REQUEST_COUNT,
    REQUEST_DURATION,
    bind_observability_context,
    clear_observability_context,
    get_logger,
    request_id_from_header,
    scope_key_names_from_headers,
)

logger = get_logger(__name__)


def route_template_for_request(request: Request) -> str:
    """Return the matched FastAPI route template for bounded metrics labels."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"


def status_class(status_code: int) -> str:
    """Return a bounded HTTP status class label."""
    return f"{status_code // 100}xx"


class ObservabilityMiddleware:
    """Middleware for HTTP request observability.

    Tracks:
    - Request count (total, by status code, by endpoint)
    - Request duration
    - Error rates
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize pure-ASGI middleware."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an ASGI request and record metrics after the final body chunk."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        start_time = time.perf_counter()
        method = str(scope.get("method") or "GET")
        request_id = request_id_from_header(request.headers.get("x-request-id"))
        scope_keys = scope_key_names_from_headers(request.headers)
        bind_observability_context(
            request_id=request_id,
            scope_keys=scope_keys,
        )
        status_code = 500
        recorded = False

        async def record_once(*, failed: bool = False, error_type: str | None = None) -> None:
            nonlocal recorded
            if recorded:
                return
            recorded = True
            duration = time.perf_counter() - start_time
            endpoint = route_template_for_request(Request(scope))
            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status="5xx" if failed else status_class(status_code),
            ).inc()

            log_fields = {
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "duration_ms": round(duration * 1000, 2),
            }
            if failed:
                logger.exception(
                    "HTTP request failed",
                    **log_fields,
                    error_type=error_type or "Exception",
                )
            else:
                logger.info("HTTP request", **log_fields)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                await record_once()

        try:
            await self.app(scope, receive, send_wrapper)
            await record_once()
        except Exception as exc:
            await record_once(failed=True, error_type=type(exc).__name__)
            raise
        finally:
            clear_observability_context()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Compatibility helper for direct unit tests.

        Runtime requests use the pure-ASGI ``__call__`` path above so streaming
        duration is measured when the final response body is sent.
        """
        start_time = time.perf_counter()
        method = request.method
        request_id = request_id_from_header(request.headers.get("x-request-id"))
        scope_keys = scope_key_names_from_headers(request.headers)
        bind_observability_context(
            request_id=request_id,
            scope_keys=scope_keys,
        )

        try:
            response = cast(Response, await call_next(request))
            status_code = response.status_code
            endpoint = route_template_for_request(request)
            response.headers["X-Request-ID"] = request_id

            duration = time.perf_counter() - start_time
            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status=status_class(status_code),
            ).inc()

            logger.info(
                "HTTP request",
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                duration_ms=round(duration * 1000, 2),
            )

            return response

        except Exception as e:
            duration = time.perf_counter() - start_time
            endpoint = route_template_for_request(request)
            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status="5xx",
            ).inc()

            logger.exception(
                "HTTP request failed",
                method=method,
                endpoint=endpoint,
                error_type=type(e).__name__,
                duration_ms=round(duration * 1000, 2),
            )

            raise
        finally:
            clear_observability_context()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Add security headers."""
        response = cast(Response, await call_next(request))

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        return response
