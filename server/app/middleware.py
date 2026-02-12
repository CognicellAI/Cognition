"""FastAPI middleware for observability."""

from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from server.app.observability import REQUEST_COUNT, REQUEST_DURATION, get_logger

logger = get_logger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware for HTTP request observability.

    Tracks:
    - Request count (total, by status code, by endpoint)
    - Request duration
    - Error rates
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with observability tracking."""
        start_time = time.time()

        # Extract endpoint info
        method = request.method
        endpoint = request.url.path

        try:
            response = await call_next(request)
            status_code = response.status_code

            # Record metrics
            duration = time.time() - start_time
            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status=str(status_code),
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
            duration = time.time() - start_time
            REQUEST_DURATION.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

            REQUEST_COUNT.labels(
                method=method,
                endpoint=endpoint,
                status="500",
            ).inc()

            # Log error
            logger.exception(
                "HTTP request failed",
                method=method,
                endpoint=endpoint,
                error=str(e),
                duration_ms=round(duration * 1000, 2),
            )

            raise


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Add security headers."""
        response = await call_next(request)

        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        return response
