"""Observability utilities for Cognition.

Provides structured logging, OpenTelemetry tracing, and metrics collection.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TypeVar

import structlog
from prometheus_client import Counter, Histogram, start_http_server

# Optional imports with fallbacks
try:
    from prometheus_client import Counter, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None
    Histogram = None
    start_http_server = None

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # Try different OTLP exporters
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            OTLPSpanExporter = None

    # Instrumentation imports
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        FastAPIInstrumentor = None

    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor
    except ImportError:
        LangchainInstrumentor = None

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    trace = None
    OTLPSpanExporter = None
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None
    FastAPIInstrumentor = None
    LangchainInstrumentor = None

# Type variable for generic function decorator
F = TypeVar("F", bound=Callable[..., Any])

# Metrics (with fallback if prometheus not available)
if PROMETHEUS_AVAILABLE:
    REQUEST_COUNT = Counter(
        "cognition_requests_total", "Total requests", ["method", "endpoint", "status"]
    )

    REQUEST_DURATION = Histogram(
        "cognition_request_duration_seconds", "Request duration in seconds", ["method", "endpoint"]
    )

    LLM_CALL_DURATION = Histogram(
        "cognition_llm_call_duration_seconds", "LLM API call duration", ["provider", "model"]
    )

    TOOL_CALL_COUNT = Counter(
        "cognition_tool_calls_total", "Total tool calls", ["tool_name", "status"]
    )

    SESSION_COUNT = Counter(
        "cognition_sessions_total",
        "Session lifecycle events",
        ["event_type"],  # created, resumed, closed, expired
    )
else:
    # Dummy metrics that do nothing
    class DummyMetric:
        def labels(self, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

    REQUEST_COUNT = DummyMetric()
    REQUEST_DURATION = DummyMetric()
    LLM_CALL_DURATION = DummyMetric()
    TOOL_CALL_COUNT = DummyMetric()
    SESSION_COUNT = DummyMetric()


def setup_tracing(
    service_name: str = "cognition",
    endpoint: str | None = None,
    app: Any | None = None,
    enabled: bool = True,
) -> None:
    """Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of the service for trace identification
        endpoint: OTLP endpoint URL (e.g., "http://localhost:4317")
        app: FastAPI application instance to instrument
        enabled: Whether to enable tracing (defaults to True)
    """
    logger = structlog.get_logger()

    if not enabled:
        logger.debug("OpenTelemetry tracing disabled by settings")
        return

    if not OPENTELEMETRY_AVAILABLE or Resource is None or TracerProvider is None:
        logger.debug("OpenTelemetry not available, skipping tracing setup")
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if endpoint and OTLPSpanExporter and BatchSpanProcessor:
        # Check if using HTTP or gRPC based on endpoint schema
        if "http" in endpoint and not endpoint.endswith("/v1/traces"):
            # Append path for HTTP exporter if missing
            if ":4318" in endpoint:
                endpoint = f"{endpoint}/v1/traces"

        exporter = OTLPSpanExporter(endpoint=endpoint)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

    if trace:
        trace.set_tracer_provider(provider)

    # Instrument FastAPI
    if app and FastAPIInstrumentor:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    # Instrument LangChain
    if LangchainInstrumentor:
        LangchainInstrumentor().instrument(tracer_provider=provider)


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


def setup_metrics(port: int = 9090, enabled: bool = True) -> None:
    """Start Prometheus metrics server.

    Args:
        port: Port to expose metrics on
        enabled: Whether to enable metrics server (defaults to True)
    """
    logger = structlog.get_logger()

    if not enabled:
        logger.debug("Prometheus metrics disabled by settings")
        return

    if not PROMETHEUS_AVAILABLE or start_http_server is None:
        logger.debug("Prometheus not available, skipping metrics server")
        return

    start_http_server(port)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Structured logger instance
    """
    return structlog.get_logger(name)


def get_tracer(name: str) -> Any:
    """Get an OpenTelemetry tracer.

    Args:
        name: Tracer name

    Returns:
        Tracer instance or None if OpenTelemetry not available
    """
    if not OPENTELEMETRY_AVAILABLE or trace is None:
        return None
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
        # If OpenTelemetry not available, just return the function unchanged
        if not OPENTELEMETRY_AVAILABLE:
            return func

        tracer = get_tracer(func.__module__)
        if tracer is None:
            return func

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
    # If OpenTelemetry not available, yield None
    if not OPENTELEMETRY_AVAILABLE or trace is None:
        yield None
        return

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span_obj:
        if attributes:
            for key, value in attributes.items():
                span_obj.set_attribute(key, value)
        yield span_obj


# Import asyncio here to avoid circular import issues
import asyncio
