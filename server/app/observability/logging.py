"""Structured logging setup with OpenTelemetry correlation."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace

if TYPE_CHECKING:
    from server.app.settings import Settings


def _add_trace_context(logger, method_name, event_dict):
    """Add OpenTelemetry trace context to log entries.

    This processor injects trace_id, span_id, and trace_flags into every
    log message, enabling correlation between logs and traces.
    """
    span_context = trace.get_current_span().get_span_context()

    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
        event_dict["trace_flags"] = format(span_context.trace_flags, "02x")

    return event_dict


def setup_logging(settings: Settings) -> None:
    """Configure structlog with OpenTelemetry correlation and proper formatting.

    Must be called BEFORE setup_tracing() so that early log messages are captured.

    In production (debug=false):
    - JSON formatted logs with trace context
    - Standard library logging bridged to structlog

    In debug mode:
    - Console formatted logs with colors
    - Pretty-printed trace context
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_trace_context,
        structlog.processors.dict_tracebacks,
    ]

    if settings.debug:
        # Debug mode: console rendering with colors
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                logging.DEBUG if settings.log_level == "debug" else logging.INFO
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Production: JSON rendering with trace correlation
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper(), logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # Bridge standard library logging to structlog
    # This ensures logs from third-party libraries (uvicorn, fastapi, etc.)
    # also get the structlog treatment with trace correlation
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    # Replace standard library loggers with structlog wrappers
    # This ensures logs from third-party libraries (uvicorn, fastapi, etc.)
    # also get the structlog treatment with trace correlation
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    # Replace standard library loggers with structlog wrappers
    final_renderer = (
        structlog.processors.JSONRenderer()
        if not settings.debug
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ExtraAdder(),
            final_renderer,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Get the structlog logger to log setup completion
    logger = structlog.get_logger()
    logger.info(
        "Logging configured",
        log_level=settings.log_level,
        debug_mode=settings.debug,
        trace_correlation=True,
    )
