"""FastAPI middleware for request correlation and WebSocket tracing."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response

logger = structlog.get_logger()


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware for request/response correlation and WebSocket lifecycle tracing.

    This middleware handles:
    1. Request ID generation and propagation
    2. Span attribute enrichment (session_id, project_id)
    3. Logging context binding
    4. Exception recording on spans

    For WebSocket connections, the trace context is established on connection
    and enriched when session/project IDs become available via message parsing.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process HTTP request with correlation context."""
        # Generate request ID
        request_id = str(uuid.uuid4())

        # Bind to structlog context (all logs in this request will include it)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Get current span and enrich with request metadata
        current_span = trace.get_current_span()
        current_span.set_attribute("http.request.id", request_id)

        # Extract session/project IDs from path params if available
        session_id = request.path_params.get("session_id")
        project_id = request.path_params.get("project_id")

        if session_id:
            current_span.set_attribute("cognition.session_id", session_id)
            structlog.contextvars.bind_contextvars(session_id=session_id)

        if project_id:
            current_span.set_attribute("cognition.project_id", project_id)
            structlog.contextvars.bind_contextvars(project_id=project_id)

        # Log request start
        logger.debug(
            "HTTP request started",
            method=request.method,
            path=request.url.path,
            request_id=request_id,
        )

        try:
            response = await call_next(request)

            # Record response on span
            current_span.set_attribute("http.response.status_code", response.status_code)

            logger.debug(
                "HTTP request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                request_id=request_id,
            )

            return response

        except Exception as e:
            # Record exception on span
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, description=str(e)))

            logger.error(
                "HTTP request failed",
                method=request.method,
                path=request.url.path,
                request_id=request_id,
                error=str(e),
            )
            raise

        finally:
            # Clear context vars to prevent leakage
            structlog.contextvars.clear_contextvars()


class WebSocketTracingMiddleware:
    """Middleware for WebSocket connection tracing.

    WebSocket connections are long-lived, so we create a parent span for the
    connection and child spans for individual messages/turns.

    Note: This is designed to wrap the WebSocket endpoint handler, not as
    standard ASGI middleware. Use in conjunction with manual span creation
    in the websocket_endpoint function.
    """

    def __init__(self) -> None:
        self._connection_spans: dict[str, trace.Span] = {}

    def start_connection(self, connection_id: str) -> trace.Span:
        """Start a span for a WebSocket connection.

        Args:
            connection_id: Unique connection identifier

        Returns:
            The connection span (caller should use as parent for child spans)
        """
        tracer = trace.get_tracer(__name__)
        span = tracer.start_span(
            name="websocket.connection",
            attributes={
                "websocket.connection_id": connection_id,
            },
        )
        self._connection_spans[connection_id] = span

        # Bind to structlog context
        structlog.contextvars.clear_context_vars()
        structlog.contextvars.bind_contextvars(connection_id=connection_id)

        logger.debug("WebSocket connection span started", connection_id=connection_id)
        return span

    def enrich_connection(
        self, connection_id: str, session_id: str | None = None, project_id: str | None = None
    ) -> None:
        """Enrich the connection span with session/project info.

        Called when create_session message is received and IDs are known.
        """
        span = self._connection_spans.get(connection_id)
        if not span:
            return

        if session_id:
            span.set_attribute("cognition.session_id", session_id)
            structlog.contextvars.bind_contextvars(session_id=session_id)

        if project_id:
            span.set_attribute("cognition.project_id", project_id)
            structlog.contextvars.bind_contextvars(project_id=project_id)

        logger.debug(
            "WebSocket connection enriched",
            connection_id=connection_id,
            session_id=session_id,
            project_id=project_id,
        )

    def start_turn(self, connection_id: str, turn_number: int, message_content: str) -> trace.Span:
        """Start a span for an agent turn (user message -> response).

        Args:
            connection_id: The WebSocket connection ID
            turn_number: Sequential turn number
            message_content: The user's message (truncated for span attribute)

        Returns:
            The turn span (use as parent for agent execution)
        """
        connection_span = self._connection_spans.get(connection_id)
        if not connection_span:
            # No parent span, create orphan span
            tracer = trace.get_tracer(__name__)
            return tracer.start_span(name="agent.turn", attributes={"turn.number": turn_number})

        # Create child span of connection
        ctx = trace.set_span_in_context(connection_span)
        tracer = trace.get_tracer(__name__)
        span = tracer.start_span(
            name="agent.turn",
            context=ctx,
            attributes={
                "turn.number": turn_number,
                "turn.message_preview": message_content[:100],
            },
        )

        structlog.contextvars.bind_contextvars(turn_number=turn_number)
        logger.debug(
            "Agent turn span started", connection_id=connection_id, turn_number=turn_number
        )
        return span

    def end_turn(self, connection_id: str, span: trace.Span, success: bool = True) -> None:
        """End a turn span."""
        span.set_status(trace.Status(trace.StatusCode.OK if success else trace.StatusCode.ERROR))
        span.end()
        logger.debug("Agent turn span ended", connection_id=connection_id, success=success)

    def end_connection(self, connection_id: str) -> None:
        """End the connection span."""
        span = self._connection_spans.pop(connection_id, None)
        if span:
            span.end()
            logger.debug("WebSocket connection span ended", connection_id=connection_id)

        # Clear context vars
        structlog.contextvars.clear_context_vars()


# Global WebSocket tracing instance
ws_tracing = WebSocketTracingMiddleware()
