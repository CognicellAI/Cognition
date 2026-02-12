"""OpenTelemetry tracing setup and configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = structlog.get_logger()

_provider: TracerProvider | None = None


def _build_resource(settings: Settings) -> Resource:
    """Build OTel resource with service attributes."""
    from opentelemetry.semconv.resource import ResourceAttributes

    return Resource.create(
        {
            ResourceAttributes.SERVICE_NAME: settings.otel_service_name,
            ResourceAttributes.SERVICE_VERSION: "0.1.0",
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "development"
            if settings.debug
            else "production",
        }
    )


def _should_enable_tracing(settings: Settings) -> bool:
    """Determine if tracing should be enabled based on settings."""
    return bool(settings.otel_enabled) or bool(
        settings.langsmith_tracing or settings.otel_exporter_otlp_endpoint
    )


def _get_langsmith_endpoint(settings: Settings) -> str:
    """Get LangSmith OTLP endpoint."""
    base = settings.langsmith_endpoint.rstrip("/")
    return f"{base}/otel/v1/traces"


def _parse_headers(header_string: str | None) -> dict[str, str]:
    """Parse key=value,key2=value2 header format."""
    if not header_string:
        return {}

    headers = {}
    for pair in header_string.split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            headers[key.strip()] = value.strip()
    return headers


def setup_tracing(app, settings: Settings) -> None:
    """Set up OpenTelemetry tracing with multi-exporter fan-out.

    Supports:
    - LangSmith (cloud or self-hosted) via OTLP
    - Custom OTLP backends (Jaeger, Grafana Tempo, Datadog, etc.)
    - Console export (debug mode)
    - Any combination of the above (fan-out)

    Deep Agents / LangGraph will automatically use this TracerProvider
    when LANGSMITH_OTEL_ENABLED is set, giving us full agent traces.
    """
    global _provider

    if not _should_enable_tracing(settings):
        logger.info("Tracing disabled (no OTEL or LangSmith configuration)")
        return

    logger.info(
        "Setting up OpenTelemetry tracing",
        service_name=settings.otel_service_name,
        langsmith_enabled=settings.langsmith_tracing,
        custom_otlp=bool(settings.otel_exporter_otlp_endpoint),
    )

    # Create resource and provider
    resource = _build_resource(settings)
    _provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(_provider)

    exporters_added = 0

    # Add LangSmith exporter if enabled
    if settings.langsmith_tracing:
        langsmith_headers = {"x-api-key": settings.langsmith_api_key}
        if settings.langsmith_project:
            langsmith_headers["Langsmith-Project"] = settings.langsmith_project

        langsmith_exporter = OTLPSpanExporter(
            endpoint=_get_langsmith_endpoint(settings),
            headers=langsmith_headers,
            timeout=10,
        )
        _provider.add_span_processor(
            BatchSpanProcessor(langsmith_exporter, schedule_delay_millis=5000)
        )
        logger.info(
            "Added LangSmith OTLP exporter",
            endpoint=settings.langsmith_endpoint,
            project=settings.langsmith_project,
        )
        exporters_added += 1

    # Add custom OTLP exporter if configured
    if settings.otel_exporter_otlp_endpoint:
        custom_headers = _parse_headers(settings.otel_exporter_otlp_headers)

        custom_exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            headers=custom_headers,
            timeout=10,
        )
        _provider.add_span_processor(
            BatchSpanProcessor(custom_exporter, schedule_delay_millis=5000)
        )
        logger.info(
            "Added custom OTLP exporter",
            endpoint=settings.otel_exporter_otlp_endpoint,
        )
        exporters_added += 1

    # Add console exporter in debug mode
    if settings.debug:
        console_exporter = ConsoleSpanExporter()
        _provider.add_span_processor(SimpleSpanProcessor(console_exporter))
        logger.info("Added console span exporter (debug mode)")

    # Instrument FastAPI (auto-captures HTTP requests)
    FastAPIInstrumentor().instrument_app(app)
    logger.info("FastAPI auto-instrumentation enabled")

    # Enable Deep Agents / LangGraph to use our provider
    import os

    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    if settings.langsmith_tracing and not settings.otel_exporter_otlp_endpoint:
        # Only sending to LangSmith, not elsewhere
        os.environ["LANGSMITH_OTEL_ONLY"] = "true"

    logger.info(
        "Tracing setup complete",
        exporters=exporters_added,
        deep_agents_otel="enabled",
    )


def shutdown_tracing() -> None:
    """Gracefully shutdown the TracerProvider and flush pending spans."""
    global _provider

    if _provider is not None:
        logger.info("Shutting down OpenTelemetry tracing")
        try:
            _provider.force_flush(timeout_millis=5000)
            _provider.shutdown()
            logger.info("Tracing shutdown complete")
        except Exception as e:
            logger.error("Error during tracing shutdown", error=str(e))
        finally:
            _provider = None


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer instance for creating custom spans.

    Args:
        name: The tracer name (typically __name__)

    Returns:
        Tracer instance
    """
    return trace.get_tracer(name)
