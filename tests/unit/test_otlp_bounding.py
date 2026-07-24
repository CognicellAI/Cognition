"""Encoded-byte OTLP export bounding regression tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from server.app.observability import (
    ByteBoundedSpanExporter,
    _encoded_span_batch_size,
)


class _RecordingExporter:
    def __init__(
        self,
        result: SpanExportResult = SpanExportResult.SUCCESS,
    ) -> None:
        self.result = result
        self.batches: list[tuple[ReadableSpan, ...]] = []
        self.shutdown_called = False
        self.flush_timeouts: list[int] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.batches.append(tuple(spans))
        return self.result

    def shutdown(self) -> None:
        self.shutdown_called = True

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        self.flush_timeouts.append(timeout_millis)
        return True


def _finished_spans(
    attributes: Sequence[dict[str, Any]],
) -> tuple[ReadableSpan, ...]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("cognition.otlp.tests")
    for index, span_attributes in enumerate(attributes):
        with tracer.start_as_current_span(
            f"span-{index}",
            attributes=span_attributes,
        ):
            pass
    provider.shutdown()
    return exporter.get_finished_spans()


def test_export_splits_by_actual_protobuf_bytes() -> None:
    spans = _finished_spans(
        [{"payload": "x" * 3000, "index": index} for index in range(8)]
    )
    single_size = max(_encoded_span_batch_size([span]) for span in spans)
    # Each span fits, while two spans do not.
    limit = single_size + 32
    assert _encoded_span_batch_size(spans[:2]) > limit

    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)

    assert exporter.export(spans) is SpanExportResult.SUCCESS
    assert len(delegate.batches) == len(spans)
    assert [
        span.name
        for batch in delegate.batches
        for span in batch
    ] == [span.name for span in spans]
    assert all(
        _encoded_span_batch_size(batch) <= limit
        for batch in delegate.batches
    )


def test_single_span_with_oversized_attribute_is_dropped_without_oversize_request() -> None:
    small, oversized = _finished_spans(
        [
            {"payload": "small"},
            {"payload": "x" * 250_000},
        ]
    )
    limit = _encoded_span_batch_size([small]) + 64
    assert _encoded_span_batch_size([oversized]) > limit

    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)

    assert exporter.export((oversized, small)) is SpanExportResult.SUCCESS
    assert len(delegate.batches) == 1
    assert delegate.batches[0] == (small,)
    assert _encoded_span_batch_size(delegate.batches[0]) <= limit


def test_no_outgoing_request_exceeds_configured_limit_for_mixed_batch() -> None:
    spans = _finished_spans(
        [
            {"payload": "a" * 100},
            {"payload": "b" * 20_000},
            {"payload": "c" * 100},
            {"payload": "d" * 20_000},
            {"payload": "e" * 500_000},
        ]
    )
    normal_sizes = [
        _encoded_span_batch_size([span])
        for span in spans[:-1]
    ]
    limit = max(normal_sizes) + 500
    assert _encoded_span_batch_size([spans[-1]]) > limit

    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)
    assert exporter.export(spans) is SpanExportResult.SUCCESS

    exported_names = [
        span.name
        for batch in delegate.batches
        for span in batch
    ]
    assert exported_names == [span.name for span in spans[:-1]]
    assert all(
        _encoded_span_batch_size(batch) <= limit
        for batch in delegate.batches
    )


def test_delegate_failure_and_lifecycle_are_propagated() -> None:
    spans = _finished_spans([{"payload": "small"}])
    delegate = _RecordingExporter(SpanExportResult.FAILURE)
    exporter = ByteBoundedSpanExporter(
        delegate,
        max_export_bytes=_encoded_span_batch_size(spans) + 10,
    )

    assert exporter.export(spans) is SpanExportResult.FAILURE
    assert exporter.force_flush(timeout_millis=1234)
    assert delegate.flush_timeouts == [1234]
    exporter.shutdown()
    assert delegate.shutdown_called


def test_invalid_or_too_small_byte_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_export_bytes"):
        ByteBoundedSpanExporter(_RecordingExporter(), max_export_bytes=0)
