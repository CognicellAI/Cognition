"""Observability utilities for Cognition.

Provides structured logging, OpenTelemetry tracing, and metrics collection.
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

import structlog

# Optional imports with fallbacks
try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Counter = None  # type: ignore[assignment,misc]
    Gauge = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]
    start_http_server = None  # type: ignore[assignment]

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )

    # Try different OTLP exporters
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            OTLPSpanExporter = None  # type: ignore[assignment,misc]

    # Instrumentation imports
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        FastAPIInstrumentor = None  # type: ignore[assignment,misc]

    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor
    except ImportError:
        LangchainInstrumentor = None  # type: ignore[assignment,misc]

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    trace = None  # type: ignore[assignment]
    OTLPSpanExporter = None  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    BatchSpanProcessor = None  # type: ignore[assignment,misc]
    SpanExporter = object  # type: ignore[assignment,misc]
    SpanExportResult = None  # type: ignore[assignment,misc]
    ReadableSpan = Any  # type: ignore[misc,assignment]
    encode_spans = None  # type: ignore[assignment]
    FastAPIInstrumentor = None  # type: ignore[assignment,misc]
    LangchainInstrumentor = None  # type: ignore[assignment,misc]

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

    TOOL_SAFETY_EVENT_COUNT = Counter(
        "cognition_tool_safety_events_total",
        "Total tool safety events",
        ["action", "tool_name"],
    )

    CONTEXT_EVENT_COUNT = Counter(
        "cognition_context_events_total",
        "Total context policy and budget events",
        ["action"],
    )

    HITL_DECISION_COUNT = Counter(
        "cognition_hitl_decisions_total",
        "Total human-in-the-loop decisions",
        ["decision", "tool_name"],
    )

    RUNTIME_EVENT_COUNT = Counter(
        "cognition_runtime_events_total",
        "Total durable runtime events",
        ["event_type", "visibility"],
    )

    RUN_TRANSITION_COUNT = Counter(
        "cognition_run_transitions_total",
        "Total durable run lifecycle transitions",
        ["status"],
    )

    SESSION_COUNT = Counter(
        "cognition_sessions_total",
        "Session lifecycle events",
        ["event_type"],  # created, resumed, closed, expired
    )
    A2A_REQUESTS_TOTAL = Counter(
        "cognition_a2a_requests_total",
        "A2A operations by outcome",
        ["operation", "outcome"],
    )
    RUNTIME_TASK_TRANSITIONS_TOTAL = Counter(
        "cognition_runtime_task_transitions_total",
        "Durable task lifecycle transitions",
        ["transport", "status"],
    )
    RUNTIME_ACTIVE_TASKS = Gauge(
        "cognition_runtime_active_tasks",
        "Task executions active in this process",
        ["transport"],
    )
    A2A_ACTIVE_SUBSCRIBERS = Gauge(
        "cognition_a2a_active_subscribers",
        "A2A subscribers active in this process",
    )
    RUNTIME_TIME_TO_FIRST_OUTPUT = Histogram(
        "cognition_runtime_time_to_first_output_seconds",
        "Time from task execution start to first output",
        ["transport"],
    )
    RUNTIME_TASK_DURATION = Histogram(
        "cognition_runtime_task_duration_seconds",
        "Task execution duration",
        ["transport", "outcome"],
    )
    A2A_STREAM_CHUNK_BYTES = Histogram(
        "cognition_a2a_stream_chunk_bytes",
        "Encoded bytes per emitted A2A artifact chunk",
    )
    A2A_STREAM_FLUSH_DURATION = Histogram(
        "cognition_a2a_stream_flush_duration_seconds",
        "Time required to persist and emit one A2A stream chunk",
    )
    A2A_SUBSCRIPTIONS_TOTAL = Counter(
        "cognition_a2a_subscriptions_total",
        "A2A subscription lifecycle events",
        ["outcome"],
    )
    A2A_IDEMPOTENCY_TOTAL = Counter(
        "cognition_a2a_idempotency_total",
        "A2A idempotency outcomes",
        ["outcome"],
    )
    A2A_LIMIT_REJECTIONS_TOTAL = Counter(
        "cognition_a2a_limit_rejections_total",
        "A2A requests or outputs rejected by resource limits",
        ["direction", "limit"],
    )
    RUNTIME_TASK_CLEANUP_TOTAL = Counter(
        "cognition_runtime_task_cleanup_total",
        "Durable task retention cleanup outcomes",
        ["transport", "outcome"],
    )
    RUNTIME_TASK_CLEANUP_DURATION = Histogram(
        "cognition_runtime_task_cleanup_duration_seconds",
        "Durable task retention cleanup duration",
        ["transport"],
    )
    OTLP_EXPORT_REQUEST_BYTES = Histogram(
        "cognition_otlp_export_request_bytes",
        "Encoded bytes per bounded OTLP trace export request",
    )
    OTLP_OVERSIZE_SPANS_TOTAL = Counter(
        "cognition_otlp_oversize_spans_total",
        "Spans dropped because one encoded span exceeded the OTLP request limit",
    )
    RUNTIME_CACHE_SIZE = Gauge(
        "cognition_runtime_cache_size",
        "Current entries in bounded in-process runtime caches",
        ["cache"],
    )
    RUNTIME_CACHE_EVICTIONS_TOTAL = Counter(
        "cognition_runtime_cache_evictions_total",
        "Entries evicted from bounded in-process runtime caches",
        ["cache", "reason"],
    )
else:
    # Dummy metrics that do nothing
    class DummyMetric:
        def labels(self, **kwargs: Any) -> DummyMetric:
            """Return self for chaining."""
            return self

        def inc(self, *args: Any, **kwargs: Any) -> None:
            """No-op."""

        def dec(self, *args: Any, **kwargs: Any) -> None:
            """No-op."""

        def observe(self, *args: Any, **kwargs: Any) -> None:
            """No-op."""

        def set(self, *args: Any, **kwargs: Any) -> None:
            """No-op."""

    REQUEST_COUNT = DummyMetric()  # type: ignore[assignment]
    REQUEST_DURATION = DummyMetric()  # type: ignore[assignment]
    LLM_CALL_DURATION = DummyMetric()  # type: ignore[assignment]
    TOOL_CALL_COUNT = DummyMetric()  # type: ignore[assignment]
    TOOL_SAFETY_EVENT_COUNT = DummyMetric()  # type: ignore[assignment]
    CONTEXT_EVENT_COUNT = DummyMetric()  # type: ignore[assignment]
    HITL_DECISION_COUNT = DummyMetric()  # type: ignore[assignment]
    RUNTIME_EVENT_COUNT = DummyMetric()  # type: ignore[assignment]
    RUN_TRANSITION_COUNT = DummyMetric()  # type: ignore[assignment]
    SESSION_COUNT = DummyMetric()  # type: ignore[assignment]
    A2A_REQUESTS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_TASK_TRANSITIONS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_ACTIVE_TASKS = DummyMetric()  # type: ignore[assignment]
    A2A_ACTIVE_SUBSCRIBERS = DummyMetric()  # type: ignore[assignment]
    RUNTIME_TIME_TO_FIRST_OUTPUT = DummyMetric()  # type: ignore[assignment]
    RUNTIME_TASK_DURATION = DummyMetric()  # type: ignore[assignment]
    A2A_STREAM_CHUNK_BYTES = DummyMetric()  # type: ignore[assignment]
    A2A_STREAM_FLUSH_DURATION = DummyMetric()  # type: ignore[assignment]
    A2A_SUBSCRIPTIONS_TOTAL = DummyMetric()  # type: ignore[assignment]
    A2A_IDEMPOTENCY_TOTAL = DummyMetric()  # type: ignore[assignment]
    A2A_LIMIT_REJECTIONS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_TASK_CLEANUP_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_TASK_CLEANUP_DURATION = DummyMetric()  # type: ignore[assignment]
    OTLP_EXPORT_REQUEST_BYTES = DummyMetric()  # type: ignore[assignment]
    OTLP_OVERSIZE_SPANS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_CACHE_SIZE = DummyMetric()  # type: ignore[assignment]
    RUNTIME_CACHE_EVICTIONS_TOTAL = DummyMetric()  # type: ignore[assignment]


def _encoded_span_batch_size(spans: Sequence[ReadableSpan]) -> int:
    """Return conservative encoded gRPC request bytes for a span batch."""
    if encode_spans is None:
        raise RuntimeError("OpenTelemetry OTLP protobuf encoder is unavailable")
    request = encode_spans(spans)
    # Include the five-byte gRPC message frame. HTTP/protobuf has no larger
    # framing overhead, so this remains a safe bound for both transports.
    return len(request.SerializeToString()) + 5


class ByteBoundedSpanExporter(SpanExporter):  # type: ignore[misc]
    """Split exporter batches by their actual encoded protobuf request size."""

    def __init__(self, exporter: Any, max_export_bytes: int) -> None:
        if max_export_bytes <= 0:
            raise ValueError("max_export_bytes must be positive")
        self._exporter = exporter
        self._max_export_bytes = max_export_bytes

    def export(self, spans: Sequence[ReadableSpan]) -> Any:
        if SpanExportResult is None:
            return None
        chunks: list[list[ReadableSpan]] = []
        current: list[ReadableSpan] = []
        for span in spans:
            candidate = [*current, span]
            try:
                encoded_bytes = _encoded_span_batch_size(candidate)
            except Exception:
                structlog.get_logger().exception("Failed to encode OTLP trace batch")
                return SpanExportResult.FAILURE
            if encoded_bytes <= self._max_export_bytes:
                current = candidate
                continue

            if current:
                chunks.append(current)
                current = []
            single_bytes = _encoded_span_batch_size([span])
            if single_bytes > self._max_export_bytes:
                OTLP_OVERSIZE_SPANS_TOTAL.inc()
                structlog.get_logger().warning(
                    "Dropping oversize OTLP span",
                    encoded_bytes=single_bytes,
                    max_export_bytes=self._max_export_bytes,
                )
                continue
            current = [span]
        if current:
            chunks.append(current)

        for chunk in chunks:
            encoded_bytes = _encoded_span_batch_size(chunk)
            if encoded_bytes > self._max_export_bytes:
                return SpanExportResult.FAILURE
            OTLP_EXPORT_REQUEST_BYTES.observe(encoded_bytes)
            result = self._exporter.export(tuple(chunk))
            if result is not SpanExportResult.SUCCESS:
                return result
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        force_flush = getattr(self._exporter, "force_flush", None)
        if force_flush is None:
            return True
        return bool(force_flush(timeout_millis=timeout_millis))


def setup_tracing(
    service_name: str = "cognition",
    endpoint: str | None = None,
    app: Any | None = None,
    enabled: bool = True,
    max_export_bytes: int = 3_670_016,
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

        exporter = ByteBoundedSpanExporter(
            OTLPSpanExporter(endpoint=endpoint),
            max_export_bytes=max_export_bytes,
        )
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

    if trace:
        trace.set_tracer_provider(provider)

    # Instrument FastAPI
    if app and FastAPIInstrumentor:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    # Instrument LangChain
    if LangchainInstrumentor:
        instrument_signature = inspect.signature(LangchainInstrumentor().instrument)
        supports_module_kwarg = False

        wrapped = getattr(LangchainInstrumentor, "_instrument", None)
        if wrapped is not None:
            try:
                supports_module_kwarg = "module" in inspect.getsource(wrapped)
            except (OSError, TypeError):
                supports_module_kwarg = False

        if supports_module_kwarg:
            logger.warning(
                "Skipping LangChain OpenTelemetry instrumentation due to incompatible wrapt API"
            )
        elif "tracer_provider" in instrument_signature.parameters:
            LangchainInstrumentor().instrument(tracer_provider=provider)
        else:
            LangchainInstrumentor().instrument()


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


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Structured logger instance
    """
    return structlog.get_logger(name)  # type: ignore[return-value]


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


def current_trace_context() -> tuple[str | None, str | None]:
    """Return current OpenTelemetry trace/span ids as hex strings if available."""
    if not OPENTELEMETRY_AVAILABLE or trace is None:
        return None, None
    span_obj = trace.get_current_span()
    context = span_obj.get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


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

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper  # type: ignore[return-value]

    return decorator


def timed(metric: Any, labels: dict[str, str] | None = None) -> Callable[[F], F]:
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

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Any:
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
