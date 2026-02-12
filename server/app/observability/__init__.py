"""Observability utilities for Cognition.

Provides structured logging, OpenTelemetry tracing, and metrics collection.
"""

from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, start_http_server

# Type variable for generic function decorator
F = TypeVar("F", bound=Callable[..., Any])

# Metrics
REQUEST_COUNT = Counter(
    "cognition_requests_total", "Total requests", ["method", "endpoint", "status"]
)

REQUEST_DURATION = Histogram(
    "cognition_request_duration_seconds", "Request duration in seconds", ["method", "endpoint"]
)

LLM_CALL_DURATION = Histogram(
    "cognition_llm_call_duration_seconds", "LLM API call duration", ["provider", "model"]
)

TOOL_CALL_COUNT = Counter("cognition_tool_calls_total", "Total tool calls", ["tool_name", "status"])

SESSION_COUNT = Counter(
    "cognition_sessions_total",
    "Session lifecycle events",
    ["event_type"],  # created, resumed, closed, expired
)


def setup_tracing(service_name: str = "cognition", endpoint: str | None = None) -> None:
    """Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of the service for trace identification
        endpoint: OTLP endpoint URL (e.g., "http://localhost:4317")
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)


def setup_logging(log_level: str = "info", json_format: bool = False) -> None:
    """Configure structured logging.

    Args:
        log_level: Logging level (debug, info, warning, error)
        json_format: Whether to output JSON formatted logs
    """
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def setup_metrics(port: int = 9090) -> None:
    """Start Prometheus metrics server.

    Args:
        port: Port to expose metrics on
    """
    start_http_server(port)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Structured logger instance
    """
    return structlog.get_logger(name)


def get_tracer(name: str) -> trace.Tracer:
    """Get an OpenTelemetry tracer.

    Args:
        name: Tracer name

    Returns:
        Tracer instance
    """
    return trace.get_tracer(name)


def traced(name: str | None = None) -> Callable[[F], F]:
    """Decorator to add tracing to a function.

    Args:
        name: Span name (defaults to function name)

    Example:
        @traced("process_request")
        async def handle_request(req: Request) -> Response:
            ...
    """

    def decorator(func: F) -> F:
        tracer = get_tracer(func.__module__)
        span_name = name or func.__name__

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(span_name):
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(span_name):
                return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper  # type: ignore

    return decorator


def timed(metric: Histogram, labels: dict[str, str] | None = None) -> Callable[[F], F]:
    """Decorator to measure function execution time.

    Args:
        metric: Histogram metric to record duration
        labels: Additional labels for the metric

    Example:
        @timed(LLM_CALL_DURATION, {"provider": "openai"})
        async def call_llm(messages: list) -> str:
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.time() - start
                metric.labels(**(labels or {})).observe(duration)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.time() - start
                metric.labels(**(labels or {})).observe(duration)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper  # type: ignore

    return decorator


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None):
    """Context manager for creating a trace span.

    Args:
        name: Span name
        attributes: Span attributes

    Example:
        with span("database_query", {"query": "SELECT * FROM users"}):
            results = db.execute(query)
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span


# Import asyncio here to avoid circular import issues
import asyncio
