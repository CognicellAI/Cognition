"""Small, content-free operation timing using only the OpenTelemetry API.

The SDK never configures providers or imports Cognition.
Operation names are constants at call sites, not resource identifiers.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

try:
    from opentelemetry import metrics, trace
    from opentelemetry.trace import StatusCode
except ImportError:  # The SDK remains usable without the optional otel extra.
    metrics = trace = None


@contextmanager
def operation(name: str) -> Iterator[None]:
    """Time an internal operation without exporting arguments or exception text."""
    if metrics is None or trace is None:
        yield
        return
    started = time.perf_counter()
    outcome = "success"
    manager: Any = None
    span: Any = None
    try:
        candidate = trace.get_tracer(__name__).start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
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
                "lambda_microvm.operation.duration",
                unit="s",
                description="Internal operation duration; operation names are bounded constants",
            ).record(elapsed, {"operation": name, "outcome": outcome})
        except Exception:
            pass
        finally:
            if manager is not None:
                with suppress(Exception):
                    manager.__exit__(None, None, None)
