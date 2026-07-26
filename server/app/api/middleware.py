"""FastAPI middleware for observability."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import cast

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from server.app.observability import (
    REQUEST_COUNT,
    REQUEST_DURATION,
    bind_observability_context,
    clear_observability_context,
    get_logger,
    request_id_from_header,
    scope_key_names_from_headers,
)
from server.app.observability import (
    span as trace_span,
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


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware for HTTP request observability.

    Tracks:
    - Request count (total, by status code, by endpoint)
    - Request duration
    - Error rates
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with observability tracking."""
        start_time = time.perf_counter()

        method = request.method
        request_id = request_id_from_header(request.headers.get("x-request-id"))
        scope_keys = scope_key_names_from_headers(request.headers)
        bind_observability_context(
            request_id=request_id,
            scope_keys=scope_keys,
        )

        with trace_span(
            "cognition.http.request",
            {
                "http.request.method": method,
                "cognition.request_id": request_id,
                "cognition.scope_keys": ",".join(scope_keys),
            },
        ) as span_obj:
            try:
                response = cast(Response, await call_next(request))
                status_code = response.status_code
                endpoint = route_template_for_request(request)
                response.headers["X-Request-ID"] = request_id
                if span_obj is not None:
                    span_obj.set_attribute("http.route", endpoint)
                    span_obj.set_attribute(
                        "http.response.status_code",
                        status_code,
                    )
                    span_obj.set_attribute(
                        "cognition.http.status_class",
                        status_class(status_code),
                    )

                # Record metrics
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

                # Log request
                logger.info(
                    "HTTP request",
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    duration_ms=round(duration * 1000, 2),
                )

                return response

            except Exception as e:
                # Record error metrics
                duration = time.perf_counter() - start_time
                endpoint = route_template_for_request(request)
                if span_obj is not None:
                    span_obj.set_attribute("http.route", endpoint)
                    span_obj.set_attribute("cognition.http.status_class", "5xx")
                    span_obj.set_attribute("error.type", type(e).__name__)
                REQUEST_DURATION.labels(
                    method=method,
                    endpoint=endpoint,
                ).observe(duration)

                REQUEST_COUNT.labels(
                    method=method,
                    endpoint=endpoint,
                    status="5xx",
                ).inc()

                # Log error
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
