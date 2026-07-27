"""Observability utilities for Cognition.

Provides structured logging, OpenTelemetry tracing, and metrics collection.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar
from uuid import uuid4

import structlog
from structlog.contextvars import (
    bind_contextvars,
    bound_contextvars,
    clear_contextvars,
    merge_contextvars,
)

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
    from opentelemetry import context as context_api
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter as GRPCOTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter as GRPCOTLPSpanExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as HTTPOTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HTTPOTLPSpanExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    from opentelemetry.trace import Link, SpanKind

    # Instrumentation imports
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        FastAPIInstrumentor = None  # type: ignore[assignment,misc]

    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor
        from opentelemetry.instrumentation.langchain.callback_handler import (
            TraceloopCallbackHandler,
        )
        from opentelemetry.instrumentation.utils import (
            _SUPPRESS_INSTRUMENTATION_KEY,
        )
        from opentelemetry.semconv_ai import Meters
    except ImportError:
        LangchainInstrumentor = None  # type: ignore[assignment,misc]
        TraceloopCallbackHandler = None  # type: ignore[assignment,misc]
        _SUPPRESS_INSTRUMENTATION_KEY = None  # type: ignore[assignment]
        Meters = None  # type: ignore[assignment,misc]

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    context_api = None  # type: ignore[assignment]
    metrics = None  # type: ignore[assignment]
    trace = None  # type: ignore[assignment]
    GRPCOTLPSpanExporter = None  # type: ignore[assignment,misc]
    HTTPOTLPSpanExporter = None  # type: ignore[assignment,misc]
    GRPCOTLPMetricExporter = None  # type: ignore[assignment,misc]
    HTTPOTLPMetricExporter = None  # type: ignore[assignment,misc]
    MeterProvider = None  # type: ignore[assignment,misc]
    PeriodicExportingMetricReader = None  # type: ignore[assignment,misc]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    BatchSpanProcessor = None  # type: ignore[assignment,misc]
    SpanExporter = object  # type: ignore[assignment,misc]
    SpanExportResult = None  # type: ignore[assignment,misc]
    ReadableSpan = Any  # type: ignore[misc,assignment]
    SpanProcessor = object  # type: ignore[assignment,misc]
    encode_spans = None  # type: ignore[assignment]
    ParentBased = None  # type: ignore[assignment,misc]
    TraceIdRatioBased = None  # type: ignore[assignment,misc]
    Link = None  # type: ignore[assignment,misc]
    SpanKind = None  # type: ignore[assignment,misc]
    FastAPIInstrumentor = None  # type: ignore[assignment,misc]
    LangchainInstrumentor = None  # type: ignore[assignment,misc]
    TraceloopCallbackHandler = None  # type: ignore[assignment,misc]
    _SUPPRESS_INSTRUMENTATION_KEY = None  # type: ignore[assignment]
    Meters = None  # type: ignore[assignment,misc]

# Type variable for generic function decorator
F = TypeVar("F", bound=Callable[..., Any])
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "password",
    "credential",
    "cookie",
    "token",
)
_RAW_SCOPE_KEYS = {"scope", "scopes", "effective_scope", "scope_key"}
_SAFE_SCOPE_KEYS = {"scope_keys", "cognition.scope.keys"}
_OBSERVABILITY_SCOPE_HMAC_KEY: str | None = None
_REDACTED = "[REDACTED]"
_TRACE_PROBE_EXCLUDED_URLS = "/health,/ready,/metrics"
_METRICS_ONLY_LANGCHAIN_SCOPE = "opentelemetry.instrumentation.langchain"
_LANGCHAIN_METRICS_CALLBACK: Any | None = None
_LANGCHAIN_METRICS_WRAPPER_INSTALLED = False
_ACTIVE_AGENT_RUN_SPAN: ContextVar[Any | None] = ContextVar(
    "cognition_active_agent_run_span",
    default=None,
)


class _OTelAsyncContextDetachNoiseFilter(logging.Filter):
    """Hide known upstream async-generator context detach warnings in standard mode."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "opentelemetry.context":
            return True
        if record.getMessage() != "Failed to detach context":
            return True
        exc = record.exc_info[1] if record.exc_info else None
        return not (isinstance(exc, ValueError) and "different Context" in str(exc))


def _install_otel_context_noise_filter(trace_detail: str) -> None:
    """Suppress upstream OpenTelemetry async-context detach noise in standard mode."""
    if trace_detail == "debug":
        return
    otel_context_logger = logging.getLogger("opentelemetry.context")
    if not any(
        isinstance(existing_filter, _OTelAsyncContextDetachNoiseFilter)
        for existing_filter in otel_context_logger.filters
    ):
        otel_context_logger.addFilter(_OTelAsyncContextDetachNoiseFilter())


# Metrics (with fallback if prometheus not available)
if PROMETHEUS_AVAILABLE:
    REQUEST_COUNT = Counter(
        "cognition_requests_total", "Total requests", ["method", "endpoint", "status"]
    )

    REQUEST_DURATION = Histogram(
        "cognition_request_duration_seconds", "Request duration in seconds", ["method", "endpoint"]
    )

    LLM_CALL_DURATION = Histogram(
        "cognition_llm_call_duration_seconds",
        "LLM API call duration",
    )

    TOOL_CALL_COUNT = Counter("cognition_tool_calls_total", "Total tool calls", ["status"])

    TOOL_SAFETY_EVENT_COUNT = Counter(
        "cognition_tool_safety_events_total",
        "Total tool safety events",
        ["action"],
    )

    CONTEXT_EVENT_COUNT = Counter(
        "cognition_context_events_total",
        "Total context policy and budget events",
        ["action"],
    )

    HITL_DECISION_COUNT = Counter(
        "cognition_hitl_decisions_total",
        "Total human-in-the-loop decisions",
        ["decision"],
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
    TELEMETRY_EXPORT_BATCHES_TOTAL = Counter(
        "cognition_telemetry_export_batches_total",
        "Telemetry export batches by signal, transport, and outcome",
        ["signal", "transport", "outcome"],
    )
    TELEMETRY_DROPPED_ITEMS_TOTAL = Counter(
        "cognition_telemetry_dropped_items_total",
        "Telemetry items dropped by signal and reason",
        ["signal", "reason"],
    )
    TELEMETRY_BATCH_SPLITS_TOTAL = Counter(
        "cognition_telemetry_batch_splits_total",
        "Telemetry batches split before export by signal and reason",
        ["signal", "reason"],
    )
    TELEMETRY_LAST_SUCCESS_UNIXTIME = Gauge(
        "cognition_telemetry_last_success_unixtime",
        "Last successful telemetry export time as a Unix timestamp",
        ["signal", "transport"],
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
    RUNTIME_CACHE_LOOKUPS_TOTAL = Counter(
        "cognition_runtime_cache_lookups_total",
        "Bounded in-process runtime cache lookups by outcome and reason",
        ["cache", "outcome", "reason"],
    )
    RUNTIME_MANIFEST_RESOLUTIONS_TOTAL = Counter(
        "cognition_runtime_manifest_resolutions_total",
        "Runtime manifest resolution attempts by outcome",
        ["outcome"],
    )
    RUNTIME_MANIFEST_RESOLUTION_DURATION = Histogram(
        "cognition_runtime_manifest_resolution_seconds",
        "Runtime manifest resolution duration by outcome",
        ["outcome"],
    )
    STORAGE_OPERATIONS_TOTAL = Counter(
        "cognition_storage_operations_total",
        "Scoped storage operations by backend, operation, and result",
        ["backend", "operation", "result"],
    )
    STORAGE_OPERATION_DURATION = Histogram(
        "cognition_storage_operation_duration_seconds",
        "Scoped storage operation duration by backend and operation",
        ["backend", "operation"],
    )
    SCOPE_ACCESS_DENIED_TOTAL = Counter(
        "cognition_scope_access_denied_total",
        "Scope access rejections by resource type and operation",
        ["resource_type", "operation"],
    )
    SANDBOX_LIFECYCLE_TOTAL = Counter(
        "cognition_sandbox_lifecycle_total",
        "Sandbox lifecycle operations by backend, stage, and outcome",
        ["backend", "stage", "outcome"],
    )
    SANDBOX_LIFECYCLE_DURATION = Histogram(
        "cognition_sandbox_lifecycle_duration_seconds",
        "Sandbox lifecycle operation duration by backend and stage",
        ["backend", "stage"],
    )
    STRICT_EXECUTION_REJECTIONS_TOTAL = Counter(
        "cognition_strict_execution_rejections_total",
        "Strict execution rejections by bounded reason",
        ["reason"],
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
    TELEMETRY_EXPORT_BATCHES_TOTAL = DummyMetric()  # type: ignore[assignment]
    TELEMETRY_DROPPED_ITEMS_TOTAL = DummyMetric()  # type: ignore[assignment]
    TELEMETRY_BATCH_SPLITS_TOTAL = DummyMetric()  # type: ignore[assignment]
    TELEMETRY_LAST_SUCCESS_UNIXTIME = DummyMetric()  # type: ignore[assignment]
    RUNTIME_CACHE_SIZE = DummyMetric()  # type: ignore[assignment]
    RUNTIME_CACHE_EVICTIONS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_CACHE_LOOKUPS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_MANIFEST_RESOLUTIONS_TOTAL = DummyMetric()  # type: ignore[assignment]
    RUNTIME_MANIFEST_RESOLUTION_DURATION = DummyMetric()  # type: ignore[assignment]
    STORAGE_OPERATIONS_TOTAL = DummyMetric()  # type: ignore[assignment]
    STORAGE_OPERATION_DURATION = DummyMetric()  # type: ignore[assignment]
    SCOPE_ACCESS_DENIED_TOTAL = DummyMetric()  # type: ignore[assignment]
    SANDBOX_LIFECYCLE_TOTAL = DummyMetric()  # type: ignore[assignment]
    SANDBOX_LIFECYCLE_DURATION = DummyMetric()  # type: ignore[assignment]
    STRICT_EXECUTION_REJECTIONS_TOTAL = DummyMetric()  # type: ignore[assignment]


def request_id_from_header(value: str | None) -> str:
    """Return a safe request id from a trusted header or generate a new one."""
    if value:
        candidate = value.strip()
        if _REQUEST_ID_RE.fullmatch(candidate):
            return candidate
    return uuid4().hex


def scope_key_names_from_headers(headers: Any) -> list[str]:
    """Return sorted Cognition scope key names from request headers."""
    prefix = "x-cognition-scope-"
    keys: set[str] = set()
    for header_name in headers.keys():
        normalized = str(header_name).lower()
        if normalized.startswith(prefix):
            key = normalized.removeprefix(prefix).replace("-", "_")
            if key:
                keys.add(key)
    return sorted(keys)


def bind_observability_context(**fields: Any) -> None:
    """Bind redacted correlation fields to the current async context."""
    safe_fields = {key: _redact_value(key, value) for key, value in fields.items()}
    bind_contextvars(**safe_fields)


@contextmanager
def observability_context(**fields: Any) -> Any:
    """Temporarily bind redacted correlation fields without clearing outer context."""
    safe_fields = {key: _redact_value(key, value) for key, value in fields.items()}
    with bound_contextvars(**safe_fields):
        yield


def clear_observability_context() -> None:
    """Clear request-scoped observability context variables."""
    clear_contextvars()


def storage_backend_label(store: Any) -> str:
    """Return a bounded backend label for storage metrics."""
    name = type(store).__name__.lower()
    if "postgres" in name:
        return "postgres"
    if "sqlite" in name:
        return "sqlite"
    if "memory" in name:
        return "memory"
    return "other"


def _redact_value(key: str, value: Any) -> Any:
    """Redact known sensitive/raw-scope fields before rendering telemetry."""
    normalized = key.lower()
    if normalized in _SAFE_SCOPE_KEYS:
        return value
    if normalized in _RAW_SCOPE_KEYS or any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, dict):
        return {
            item_key: _redact_value(str(item_key), item_value)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(key, item) for item in value)
    return value


def redact_event_fields(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor that removes secrets and raw scopes from log fields."""
    return {key: _redact_value(str(key), value) for key, value in event_dict.items()}


def add_trace_context(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Structlog processor that adds active trace/span ids when available."""
    trace_id, span_id = current_trace_context()
    if trace_id and "trace_id" not in event_dict:
        event_dict["trace_id"] = trace_id
    if span_id and "span_id" not in event_dict:
        event_dict["span_id"] = span_id
    return event_dict


def _encoded_span_batch_size(spans: Sequence[ReadableSpan]) -> int:
    """Return conservative encoded gRPC request bytes for a span batch."""
    if encode_spans is None:
        raise RuntimeError("OpenTelemetry OTLP protobuf encoder is unavailable")
    request = encode_spans(spans)
    # Include the five-byte gRPC message frame. HTTP/protobuf has no larger
    # framing overhead, so this remains a safe bound for both transports.
    return len(request.SerializeToString()) + 5


def _is_error_span(span: ReadableSpan) -> bool:
    status = getattr(span, "status", None)
    status_code = getattr(status, "status_code", None)
    return getattr(status_code, "name", "") == "ERROR"


def _should_drop_span(span: ReadableSpan, trace_detail: str) -> bool:
    """Return whether a span is routine framework noise in standard mode."""
    instrumentation_scope = getattr(span, "instrumentation_scope", None)
    instrumentation_name = getattr(instrumentation_scope, "name", None)
    if instrumentation_name == _METRICS_ONLY_LANGCHAIN_SCOPE:
        return True
    # Native LangSmith spans form the semantic LangGraph tree. Dropping an
    # intermediate hook by name would leave its exported children orphaned.
    if instrumentation_name == "langsmith":
        return False
    if trace_detail == "debug" or _is_error_span(span):
        return False
    name = span.name.lower()
    if "middleware" in name:
        return True
    return name.startswith("execute_task ") and (".before_" in name or ".after_" in name)


def _scope_fingerprint(scope: Mapping[str, str] | None) -> str | None:
    """Return an operator-keyed pseudonymous scope fingerprint."""
    if not _OBSERVABILITY_SCOPE_HMAC_KEY or not scope:
        return None
    canonical_scope = {
        str(key): str(value)
        for key, value in scope.items()
        if key is not None and value is not None
    }
    if not canonical_scope:
        return None
    payload = json.dumps(canonical_scope, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        _OBSERVABILITY_SCOPE_HMAC_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class CuratingSpanExporter(SpanExporter):  # type: ignore[misc]
    """Drop routine spans before OTLP export."""

    def __init__(
        self,
        exporter: Any,
        *,
        trace_detail: str,
    ) -> None:
        self._exporter = exporter
        self._trace_detail = trace_detail

    def export(self, spans: Sequence[ReadableSpan]) -> Any:
        curated_spans = [
            span for span in spans if not _should_drop_span(span, self._trace_detail)
        ]
        if not curated_spans:
            return SpanExportResult.SUCCESS if SpanExportResult is not None else None
        return self._exporter.export(tuple(curated_spans))

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        force_flush = getattr(self._exporter, "force_flush", None)
        if force_flush is None:
            return True
        return bool(force_flush(timeout_millis=timeout_millis))


class CuratingSpanProcessor(SpanProcessor):  # type: ignore[misc]
    """Curate spans before they enter the delegated batch queue."""

    def __init__(
        self,
        processor: Any,
        *,
        trace_detail: str,
    ) -> None:
        self._processor = processor
        self._trace_detail = trace_detail

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        """Delegate start notifications without mutation."""
        on_start = getattr(self._processor, "on_start", None)
        if on_start is not None:
            on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        """Drop routine spans before queueing."""
        if _should_drop_span(span, self._trace_detail):
            return
        self._processor.on_end(span)

    def shutdown(self) -> None:
        """Shut down the delegated processor."""
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Flush the delegated processor."""
        return bool(self._processor.force_flush(timeout_millis=timeout_millis))


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
                TELEMETRY_EXPORT_BATCHES_TOTAL.labels(
                    signal="traces",
                    transport="otlp",
                    outcome="encode_failure",
                ).inc()
                structlog.get_logger().exception("Failed to encode OTLP trace batch")
                return SpanExportResult.FAILURE
            if encoded_bytes <= self._max_export_bytes:
                current = candidate
                continue

            if current:
                chunks.append(current)
                TELEMETRY_BATCH_SPLITS_TOTAL.labels(
                    signal="traces",
                    reason="max_bytes",
                ).inc()
                current = []
            single_bytes = _encoded_span_batch_size([span])
            if single_bytes > self._max_export_bytes:
                OTLP_OVERSIZE_SPANS_TOTAL.inc()
                TELEMETRY_DROPPED_ITEMS_TOTAL.labels(
                    signal="traces",
                    reason="oversize_span",
                ).inc()
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
                TELEMETRY_EXPORT_BATCHES_TOTAL.labels(
                    signal="traces",
                    transport="otlp",
                    outcome="failure",
                ).inc()
                return result
            TELEMETRY_EXPORT_BATCHES_TOTAL.labels(
                signal="traces",
                transport="otlp",
                outcome="success",
            ).inc()
            TELEMETRY_LAST_SUCCESS_UNIXTIME.labels(
                signal="traces",
                transport="otlp",
            ).set(time.time())
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        force_flush = getattr(self._exporter, "force_flush", None)
        if force_flush is None:
            return True
        return bool(force_flush(timeout_millis=timeout_millis))


def _is_http_otlp_endpoint(endpoint: str) -> bool:
    return (
        ":4318" in endpoint or endpoint.endswith("/v1/traces") or endpoint.endswith("/v1/metrics")
    )


def _trace_endpoint(endpoint: str) -> str:
    if endpoint.endswith("/v1/traces"):
        return endpoint
    if endpoint.endswith("/v1/metrics"):
        return endpoint.removesuffix("/v1/metrics") + "/v1/traces"
    if ":4318" in endpoint:
        return endpoint.rstrip("/") + "/v1/traces"
    return endpoint


def _metric_endpoint(endpoint: str) -> str:
    if endpoint.endswith("/v1/metrics"):
        return endpoint
    if endpoint.endswith("/v1/traces"):
        return endpoint.removesuffix("/v1/traces") + "/v1/metrics"
    if ":4318" in endpoint:
        return endpoint.rstrip("/") + "/v1/metrics"
    return endpoint


def _create_trace_exporter(endpoint: str) -> Any:
    if _is_http_otlp_endpoint(endpoint):
        if HTTPOTLPSpanExporter is None:
            return None
        return HTTPOTLPSpanExporter(endpoint=_trace_endpoint(endpoint))
    if GRPCOTLPSpanExporter is None:
        return None
    return GRPCOTLPSpanExporter(endpoint=endpoint)


def _create_metric_exporter(endpoint: str) -> Any:
    if _is_http_otlp_endpoint(endpoint):
        if HTTPOTLPMetricExporter is None:
            return None
        return HTTPOTLPMetricExporter(endpoint=_metric_endpoint(endpoint))
    if GRPCOTLPMetricExporter is None:
        return None
    return GRPCOTLPMetricExporter(endpoint=endpoint)


def _enable_langsmith_otel_bridge() -> None:
    """Route native LangChain callback spans through the global OTel provider."""
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"


def _instrument_langchain_metrics(
    tracer_provider: Any,
    meter_provider: Any,
) -> None:
    """Prepare the upstream LangChain callback for standard GenAI metrics."""
    global _LANGCHAIN_METRICS_CALLBACK, _LANGCHAIN_METRICS_WRAPPER_INSTALLED
    if (
        LangchainInstrumentor is None
        or TraceloopCallbackHandler is None
        or Meters is None
        or meter_provider is None
        or context_api is None
        or _SUPPRESS_INSTRUMENTATION_KEY is None
    ):
        _LANGCHAIN_METRICS_CALLBACK = None
        return
    # Initialize the upstream content/attribute configuration without installing
    # its global callback-manager and semantic-span wrappers.
    LangchainInstrumentor(use_attributes=True)
    tracer = tracer_provider.get_tracer(_METRICS_ONLY_LANGCHAIN_SCOPE)
    meter = meter_provider.get_meter(_METRICS_ONLY_LANGCHAIN_SCOPE)
    duration_histogram = meter.create_histogram(
        name=Meters.LLM_OPERATION_DURATION,
        unit="s",
        description="GenAI operation duration",
    )
    token_histogram = meter.create_histogram(
        name=Meters.LLM_TOKEN_USAGE,
        unit="token",
        description="Measures number of input and output tokens used",
    )
    def _observe_unsuppressed(method_name: str, instance: Any, *args: Any, **kwargs: Any) -> Any:
        token = context_api.attach(
            context_api.set_value(_SUPPRESS_INSTRUMENTATION_KEY, False)
        )
        try:
            method = getattr(TraceloopCallbackHandler, method_name)
            return method(instance, *args, **kwargs)
        finally:
            context_api.detach(token)

    def _on_chat_model_start(instance: Any, *args: Any, **kwargs: Any) -> Any:
        return _observe_unsuppressed(
            "on_chat_model_start",
            instance,
            *args,
            **kwargs,
        )

    def _on_llm_start(instance: Any, *args: Any, **kwargs: Any) -> Any:
        return _observe_unsuppressed("on_llm_start", instance, *args, **kwargs)

    def _on_llm_end(instance: Any, *args: Any, **kwargs: Any) -> Any:
        return _observe_unsuppressed("on_llm_end", instance, *args, **kwargs)

    def _on_llm_error(instance: Any, *args: Any, **kwargs: Any) -> Any:
        return _observe_unsuppressed("on_llm_error", instance, *args, **kwargs)

    metrics_only_handler = type(
        "_CognitionMetricsOnlyLangChainHandler",
        (TraceloopCallbackHandler,),
        {
            "_cognition_metrics_only": True,
            "_safe_attach_context": lambda _self, _span: None,
            "get_workflow_name": lambda _self, _parent_run_id: "",
            "get_entity_path": lambda _self, _parent_run_id: "",
            "on_chat_model_start": _on_chat_model_start,
            "on_llm_start": _on_llm_start,
            "on_llm_end": _on_llm_end,
            "on_llm_error": _on_llm_error,
        },
    )
    _LANGCHAIN_METRICS_CALLBACK = metrics_only_handler(
        tracer,
        duration_histogram,
        token_histogram,
    )
    if not _LANGCHAIN_METRICS_WRAPPER_INSTALLED:
        from wrapt import wrap_function_wrapper

        def _add_metrics_handler(
            wrapped: Callable[..., Any],
            instance: Any,
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> None:
            wrapped(*args, **kwargs)
            callback = _LANGCHAIN_METRICS_CALLBACK
            if callback is None or any(
                getattr(handler, "_cognition_metrics_only", False)
                for handler in instance.inheritable_handlers
            ):
                return
            callback._callback_manager = instance
            instance.add_handler(callback, True)

        wrap_function_wrapper(
            "langchain_core.callbacks",
            "BaseCallbackManager.__init__",
            _add_metrics_handler,
        )
        _LANGCHAIN_METRICS_WRAPPER_INSTALLED = True


def langchain_metrics_callbacks() -> list[Any]:
    """Return the upstream metrics callback for an Agent invocation."""
    if _LANGCHAIN_METRICS_CALLBACK is None:
        return []
    return [_LANGCHAIN_METRICS_CALLBACK]


def setup_tracing(
    service_name: str = "cognition",
    endpoint: str | None = None,
    app: Any | None = None,
    enabled: bool = True,
    max_export_bytes: int = 3_670_016,
    queue_size: int = 2048,
    export_timeout_millis: int = 30_000,
    trace_sample_ratio: float = 1.0,
    metric_export_interval_millis: int = 60_000,
    trace_detail: str = "standard",
    observability_scope_hmac_key: str | None = None,
) -> None:
    """Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of the service for trace identification
        endpoint: OTLP endpoint URL (e.g., "http://localhost:4317")
        app: FastAPI application instance to instrument
        enabled: Whether to enable tracing (defaults to True)
    """
    logger = structlog.get_logger()
    global _OBSERVABILITY_SCOPE_HMAC_KEY
    _OBSERVABILITY_SCOPE_HMAC_KEY = observability_scope_hmac_key or None

    if not enabled:
        logger.debug("OpenTelemetry tracing disabled by settings")
        return

    if not OPENTELEMETRY_AVAILABLE or Resource is None or TracerProvider is None:
        logger.debug("OpenTelemetry not available, skipping tracing setup")
        return

    trace_detail = trace_detail.lower()
    _install_otel_context_noise_filter(trace_detail)

    resource = Resource.create({"service.name": service_name})
    meter_provider = None
    if MeterProvider is not None:
        metric_readers = []
        if endpoint:
            metric_exporter = _create_metric_exporter(endpoint)
            if metric_exporter is not None and PeriodicExportingMetricReader is not None:
                metric_readers.append(
                    PeriodicExportingMetricReader(
                        metric_exporter,
                        export_interval_millis=metric_export_interval_millis,
                    )
                )
        meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
        if metrics is not None:
            try:
                metrics.set_meter_provider(meter_provider)
            except Exception:
                logger.debug("OpenTelemetry meter provider already configured")

    clamped_sample_ratio = max(0.0, min(1.0, trace_sample_ratio))
    sampler = (
        ParentBased(TraceIdRatioBased(clamped_sample_ratio))
        if ParentBased is not None and TraceIdRatioBased is not None
        else None
    )
    provider = TracerProvider(
        resource=resource,
        **({"sampler": sampler} if sampler is not None else {}),
    )

    if endpoint and BatchSpanProcessor:
        raw_exporter = _create_trace_exporter(endpoint)
        if raw_exporter is None:
            logger.warning("OTLP trace exporter unavailable; traces will not be exported")
        else:
            bounded_exporter = ByteBoundedSpanExporter(
                raw_exporter,
                max_export_bytes=max_export_bytes,
            )
            batch_processor = BatchSpanProcessor(
                bounded_exporter,
                max_queue_size=queue_size,
                max_export_batch_size=min(512, queue_size),
                export_timeout_millis=export_timeout_millis,
            )
            processor = CuratingSpanProcessor(
                batch_processor,
                trace_detail=trace_detail,
            )
            provider.add_span_processor(processor)

    if trace:
        trace.set_tracer_provider(provider)

    # LangChain's built-in LangSmith bridge converts its native callback tree
    # to OpenTelemetry spans on Cognition's existing global provider. OTEL_ONLY
    # prevents a second direct LangSmith export path; the spans still leave
    # Cognition only through the canonical, curated OTLP exporter above.
    _enable_langsmith_otel_bridge()

    # Instrument FastAPI
    if app and FastAPIInstrumentor:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            meter_provider=meter_provider,
            excluded_urls=_TRACE_PROBE_EXCLUDED_URLS,
            exclude_spans=["receive", "send"],
        )

    # Retain OpenLLMetry's LangChain integration only for its standard GenAI
    # token and duration instruments. LangSmith's OTel-only bridge owns the
    # semantic spans, so disable the adapter's direct wrappers and synchronously
    # discard its callback spans in _should_drop_span before they reach the
    # batch queue.
    _instrument_langchain_metrics(provider, meter_provider)


def setup_logging(log_level: str = "info", json_format: bool = False) -> None:
    """Configure structured logging.

    Args:
        log_level: Logging level (debug, info, warning, error)
        json_format: Whether to output JSON formatted logs
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(message)s",
    )
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            add_trace_context,
            redact_event_fields,
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


def current_trace_context(span_obj: Any | None = None) -> tuple[str | None, str | None]:
    """Return OpenTelemetry trace/span ids for a span or the active context."""
    if not OPENTELEMETRY_AVAILABLE or trace is None:
        return None, None
    span_obj = span_obj or trace.get_current_span()
    context = span_obj.get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def add_span_event(name: str, attributes: dict[str, Any] | None = None) -> None:
    """Add an event to the active span if tracing is available."""
    if not OPENTELEMETRY_AVAILABLE or trace is None:
        return
    span_obj = trace.get_current_span()
    context = span_obj.get_span_context()
    if not context.is_valid:
        return
    span_obj.add_event(name, attributes or {})


def set_span_attributes(attributes: Mapping[str, Any]) -> None:
    """Set non-null attributes on the active span if tracing is available."""
    if not OPENTELEMETRY_AVAILABLE or trace is None:
        return
    span_obj = trace.get_current_span()
    context = span_obj.get_span_context()
    if not context.is_valid:
        return
    for key, value in attributes.items():
        if value is not None:
            span_obj.set_attribute(str(key), value)


@contextmanager
def agent_run_trace_context(span_obj: Any | None = None) -> Any:
    """Re-activate the application run span at a framework invocation boundary.

    Some async framework setup paths attach and detach their own OpenTelemetry
    context before LangGraph starts. Cognition keeps the run span in an
    independent ContextVar so the framework entry point can explicitly restore
    the intended parent without rewriting any emitted span.
    """
    if trace is None or context_api is None:
        yield
        return
    span_obj = span_obj or _ACTIVE_AGENT_RUN_SPAN.get()
    if span_obj is None or not span_obj.get_span_context().is_valid:
        yield
        return
    token = context_api.attach(trace.set_span_in_context(span_obj))
    try:
        yield
    finally:
        context_api.detach(token)


@contextmanager
def agent_run_span(
    *,
    session_id: str,
    run_id: str,
    thread_id: str,
    scope_keys: Sequence[str] | None = None,
    agent_name: str | None = None,
    agent_revision: int | None = None,
    manifest_digest: str | None = None,
    parent_run_id: str | None = None,
    effective_scope: Mapping[str, str] | None = None,
    transport: str | None = None,
    sandbox_backend: str | None = None,
) -> Any:
    """Create the application span that owns one durable Agent run trace.

    The run is intentionally detached from the transport trace because durable
    execution may outlive its REST or A2A ingress. When ingress tracing is
    active, a span link preserves that causal relationship. The run span is
    made current for the full streaming lifecycle so LangGraph, model, tool,
    and subagent auto-instrumentation inherit the same trace ID.
    """
    attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.conversation.id": thread_id,
        "session.id": session_id,
        "cognition.run.id": run_id,
        "cognition.thread.id": thread_id,
        "cognition.scope.keys": ",".join(scope_keys or []),
        "gen_ai.agent.name": agent_name or "",
        "cognition.agent.revision": agent_revision or 0,
        "cognition.manifest.digest": manifest_digest or "",
        "cognition.run.parent_id": parent_run_id or "",
    }
    if transport:
        attributes["cognition.transport"] = transport
    if sandbox_backend:
        attributes["cognition.sandbox.backend"] = sandbox_backend
    fingerprint = _scope_fingerprint(effective_scope)
    if fingerprint:
        attributes["cognition.scope.fingerprint"] = fingerprint

    tracer = get_tracer(__name__)
    if (
        tracer is None
        or trace is None
        or context_api is None
        or Link is None
        or SpanKind is None
    ):
        yield None
        return

    ingress_context = trace.get_current_span().get_span_context()
    links = [Link(ingress_context)] if ingress_context.is_valid else None
    with tracer.start_as_current_span(
        "cognition.agent.run",
        context=context_api.Context(),
        kind=SpanKind.INTERNAL,
        attributes=attributes,
        links=links,
    ) as span_obj:
        previous_span = _ACTIVE_AGENT_RUN_SPAN.get()
        _ACTIVE_AGENT_RUN_SPAN.set(span_obj)
        try:
            yield span_obj
        finally:
            # Async generators may be finalized from a copied Context. Restoring
            # the value is safe there; resetting the original token is not.
            _ACTIVE_AGENT_RUN_SPAN.set(previous_span)


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
