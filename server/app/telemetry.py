"""Small, content-free operation timing using only the OpenTelemetry API.

This foundation module never configures providers or imports application layers.
Operation names are constants at call sites, not resource identifiers.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.trace import StatusCode

_OPERATION_PARENT: ContextVar[Any] = ContextVar("operation_parent", default=None)


@contextmanager
def operation_parent(span: Any) -> Iterator[None]:
    """Bind the owning application span across framework and thread callbacks."""
    previous = _OPERATION_PARENT.get()
    _OPERATION_PARENT.set(span)
    try:
        yield
    finally:
        # Streaming generators can be finalized from a copied Context.
        _OPERATION_PARENT.set(previous)


@contextmanager
def operation_context() -> Iterator[None]:
    """Recover the owning trace when a framework installs a synthetic context.

    Valid descendants within the application's trace retain their parentage.
    This adds no spans and requires no framework-specific SDK hooks.
    """
    parent = _OPERATION_PARENT.get()
    current = trace.get_current_span().get_span_context()
    if (
        parent is not None
        and parent.get_span_context().is_valid
        and (parent.get_span_context().trace_id != current.trace_id)
    ):
        with trace.use_span(
            parent, end_on_exit=False, record_exception=False, set_status_on_exception=False
        ):
            yield
    else:
        yield


@contextmanager
def operation(name: str) -> Iterator[None]:
    """Time an internal operation without exporting arguments or exception text."""
    started = time.perf_counter()
    outcome = "success"
    manager: Any = None
    span: Any = None
    try:
        parent = _OPERATION_PARENT.get()
        current = trace.get_current_span().get_span_context()
        context = (
            trace.set_span_in_context(parent)
            if parent is not None
            and parent.get_span_context().is_valid
            and parent.get_span_context().trace_id != current.trace_id
            else None
        )
        candidate = trace.get_tracer(__name__).start_as_current_span(
            name, context=context, record_exception=False, set_status_on_exception=False
        )
        span = candidate.__enter__()
        manager = candidate
    except Exception:
        pass  # Instrumentation must not prevent the operation from executing.
    try:
        yield
    except BaseException:
        outcome = "error"
        if span is not None:
            with suppress(Exception):
                span.set_status(StatusCode.ERROR)
        raise
    finally:
        elapsed = time.perf_counter() - started
        try:
            metrics.get_meter(__name__).create_histogram(
                "cognition.operation.duration",
                unit="s",
                description="Internal operation duration; operation names are bounded constants",
            ).record(elapsed, {"operation": name, "outcome": outcome})
        except Exception:
            pass
        finally:
            if manager is not None:
                with suppress(Exception):
                    manager.__exit__(None, None, None)
