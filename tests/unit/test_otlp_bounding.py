"""Encoded-byte OTLP export bounding regression tests."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextvars import Context as ContextVarsContext
from typing import Any, cast

import pytest
from opentelemetry import context as context_api
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
    CuratingSpanExporter,
    CuratingSpanProcessor,
    _encoded_span_batch_size,
    _scope_fingerprint,
    agent_run_span,
    agent_run_trace_context,
    current_trace_context,
    setup_tracing,
)


class _Metric:
    def __init__(self) -> None:
        self.labels_seen: list[dict[str, str]] = []
        self.incremented = 0
        self.observed: list[float] = []
        self.set_values: list[float] = []

    def labels(self, **labels: str) -> _Metric:
        self.labels_seen.append(labels)
        return self

    def inc(self) -> None:
        self.incremented += 1

    def observe(self, value: float) -> None:
        self.observed.append(value)

    def set(self, value: float) -> None:
        self.set_values.append(value)


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


class _RecordingProcessor:
    def __init__(self) -> None:
        self.started = 0
        self.ended: list[ReadableSpan] = []
        self.shutdown_called = False
        self.flush_timeouts: list[int] = []

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        self.started += 1

    def on_end(self, span: ReadableSpan) -> None:
        self.ended.append(span)

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
    spans = _finished_spans([{"payload": "x" * 3000, "index": index} for index in range(8)])
    single_size = max(_encoded_span_batch_size([span]) for span in spans)
    # Each span fits, while two spans do not.
    limit = single_size + 32
    assert _encoded_span_batch_size(spans[:2]) > limit

    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)

    assert exporter.export(spans) is SpanExportResult.SUCCESS
    assert len(delegate.batches) == len(spans)
    assert [span.name for batch in delegate.batches for span in batch] == [
        span.name for span in spans
    ]
    assert all(_encoded_span_batch_size(batch) <= limit for batch in delegate.batches)


def test_export_does_not_duplicate_final_chunk() -> None:
    spans = _finished_spans([{"payload": "small", "index": index} for index in range(3)])
    limit = _encoded_span_batch_size(spans) + 128
    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)

    assert exporter.export(spans) is SpanExportResult.SUCCESS

    assert delegate.batches == [spans]


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
    normal_sizes = [_encoded_span_batch_size([span]) for span in spans[:-1]]
    limit = max(normal_sizes) + 500
    assert _encoded_span_batch_size([spans[-1]]) > limit

    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)
    assert exporter.export(spans) is SpanExportResult.SUCCESS

    exported_names = [span.name for batch in delegate.batches for span in batch]
    assert exported_names == [span.name for span in spans[:-1]]
    assert all(_encoded_span_batch_size(batch) <= limit for batch in delegate.batches)


def test_export_records_bounded_health_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    spans = _finished_spans(
        [
            {"payload": "a" * 100},
            {"payload": "b" * 20_000},
            {"payload": "c" * 500_000},
        ]
    )
    limit = max(_encoded_span_batch_size([span]) for span in spans[:-1]) + 500
    delegate = _RecordingExporter()
    exporter = ByteBoundedSpanExporter(delegate, max_export_bytes=limit)

    batches = _Metric()
    dropped = _Metric()
    splits = _Metric()
    last_success = _Metric()
    bytes_histogram = _Metric()
    monkeypatch.setattr("server.app.observability.TELEMETRY_EXPORT_BATCHES_TOTAL", batches)
    monkeypatch.setattr("server.app.observability.TELEMETRY_DROPPED_ITEMS_TOTAL", dropped)
    monkeypatch.setattr("server.app.observability.TELEMETRY_BATCH_SPLITS_TOTAL", splits)
    monkeypatch.setattr("server.app.observability.TELEMETRY_LAST_SUCCESS_UNIXTIME", last_success)
    monkeypatch.setattr("server.app.observability.OTLP_EXPORT_REQUEST_BYTES", bytes_histogram)

    assert exporter.export(spans) is SpanExportResult.SUCCESS

    assert batches.labels_seen
    assert all(
        label == {"signal": "traces", "transport": "otlp", "outcome": "success"}
        for label in batches.labels_seen
    )
    assert splits.labels_seen
    assert all(label == {"signal": "traces", "reason": "max_bytes"} for label in splits.labels_seen)
    assert dropped.labels_seen == [{"signal": "traces", "reason": "oversize_span"}]
    assert last_success.labels_seen
    assert all(
        label == {"signal": "traces", "transport": "otlp"} for label in last_success.labels_seen
    )
    assert len(bytes_histogram.observed) == len(delegate.batches)


def test_curating_exporter_preserves_raw_attributes() -> None:
    spans = _finished_spans(
        [
            {
                "gen_ai.input.messages": "secret prompt",
                "gen_ai.workflow.nodes": ["model", "tools", "VerboseMiddleware.before_model"],
                "gen_ai.workflow.edges": ["model -> tools"],
                "http.url": "https://example.com/private?token=abc",
                "cognition.scope.keys": "tenant,project",
                "cognition.run.id": "run-1",
            }
        ]
    )
    delegate = _RecordingExporter()
    exporter = CuratingSpanExporter(
        delegate,
        trace_detail="standard",
    )

    assert exporter.export(spans) is SpanExportResult.SUCCESS

    exported = delegate.batches[0][0]
    attributes = exported.attributes
    assert attributes is not None
    assert attributes["gen_ai.input.messages"] == "secret prompt"
    workflow_nodes = cast(Sequence[str], attributes["gen_ai.workflow.nodes"])
    workflow_edges = cast(Sequence[str], attributes["gen_ai.workflow.edges"])
    assert list(workflow_nodes) == [
        "model",
        "tools",
        "VerboseMiddleware.before_model",
    ]
    assert list(workflow_edges) == ["model -> tools"]
    assert attributes["http.url"] == "https://example.com/private?token=abc"
    assert attributes["cognition.scope.keys"] == "tenant,project"
    assert attributes["cognition.run.id"] == "run-1"


def test_curating_processor_preserves_raw_attributes_before_delegate_queue() -> None:
    spans = _finished_spans(
        [
            {
                "gen_ai.input.messages": "secret prompt",
                "http.url": "https://example.com/private?token=abc",
                "cognition.scope.keys": "tenant,project",
                "cognition.run.id": "run-1",
            }
        ]
    )
    delegate = _RecordingProcessor()
    processor = CuratingSpanProcessor(
        delegate,
        trace_detail="standard",
    )

    processor.on_end(spans[0])

    assert len(delegate.ended) == 1
    attributes = delegate.ended[0].attributes
    assert attributes is not None
    assert attributes["gen_ai.input.messages"] == "secret prompt"
    assert attributes["http.url"] == "https://example.com/private?token=abc"
    assert attributes["cognition.scope.keys"] == "tenant,project"
    assert attributes["cognition.run.id"] == "run-1"


def test_curating_exporter_drops_standard_middleware_success_spans() -> None:
    spans = _finished_spans(
        [
            {"payload": "kept"},
            {"payload": "dropped"},
        ]
    )
    spans[1]._name = "langchain.middleware.before_model"  # type: ignore[attr-defined]
    delegate = _RecordingExporter()
    exporter = CuratingSpanExporter(
        delegate,
        trace_detail="standard",
    )

    assert exporter.export(spans) is SpanExportResult.SUCCESS

    assert [span.name for span in delegate.batches[0]] == ["span-0"]


def test_curating_exporter_preserves_native_langsmith_parent_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("langsmith").start_as_current_span(
        "langchain.middleware.before_model"
    ):
        pass
    provider.shutdown()

    delegate = _RecordingExporter()
    curated = CuratingSpanExporter(delegate, trace_detail="standard")
    assert curated.export(exporter.get_finished_spans()) is SpanExportResult.SUCCESS

    assert [[span.name for span in batch] for batch in delegate.batches] == [
        ["langchain.middleware.before_model"]
    ]


def test_curating_exporter_drops_standard_before_after_task_spans() -> None:
    spans = _finished_spans(
        [
            {"payload": "kept-model"},
            {"payload": "dropped-before-hook"},
            {"payload": "kept-tool"},
        ]
    )
    spans[0]._name = "execute_task model"  # type: ignore[attr-defined]
    spans[1]._name = "execute_task cognition_observability.before_model"  # type: ignore[attr-defined]
    spans[2]._name = "execute_tool ls"  # type: ignore[attr-defined]
    delegate = _RecordingExporter()
    exporter = CuratingSpanExporter(
        delegate,
        trace_detail="standard",
    )

    assert exporter.export(spans) is SpanExportResult.SUCCESS

    assert [span.name for span in delegate.batches[0]] == [
        "execute_task model",
        "execute_tool ls",
    ]


def test_curating_exporter_always_drops_metrics_adapter_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("opentelemetry.instrumentation.langchain").start_as_current_span(
        "duplicate-metric-adapter-span"
    ):
        pass
    with provider.get_tracer("langsmith").start_as_current_span("native-langgraph-span"):
        pass
    provider.shutdown()

    delegate = _RecordingExporter()
    curated = CuratingSpanExporter(delegate, trace_detail="debug")
    assert curated.export(exporter.get_finished_spans()) is SpanExportResult.SUCCESS

    assert [[span.name for span in batch] for batch in delegate.batches] == [
        ["native-langgraph-span"]
    ]


def test_agent_run_span_owns_framework_trace_and_lifecycle_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("cognition.trace-parenting.tests")
    monkeypatch.setattr("server.app.observability.get_tracer", lambda _name: tracer)

    with agent_run_span(
        session_id="session-1",
        run_id="run-1",
        thread_id="thread-1",
        agent_name="reviewer",
        transport="rest",
    ) as run_span:
        assert run_span is not None
        run_context = run_span.get_span_context()
        trace_id, span_id = current_trace_context()
        assert trace_id == f"{run_context.trace_id:032x}"
        assert span_id == f"{run_context.span_id:016x}"
        run_span.add_event("cognition.run.completed", {"cognition.run.status": "done"})

        with tracer.start_as_current_span("invoke_agent LangGraph") as graph_span:
            graph_context = graph_span.get_span_context()
            assert graph_context.trace_id == run_context.trace_id

        detached_token = context_api.attach(context_api.Context())
        try:
            with agent_run_trace_context():
                with tracer.start_as_current_span(
                    "invoke_agent restored-context"
                ) as restored_span:
                    restored_context = restored_span.get_span_context()
                    assert restored_context.trace_id == run_context.trace_id
        finally:
            context_api.detach(detached_token)

    provider.shutdown()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    run = spans["cognition.agent.run"]
    graph = spans["invoke_agent LangGraph"]
    restored = spans["invoke_agent restored-context"]
    assert run.parent is None
    assert graph.parent is not None
    assert graph.parent.span_id == run.context.span_id
    assert graph.context.trace_id == run.context.trace_id
    assert restored.parent is not None
    assert restored.parent.span_id == run.context.span_id
    assert restored.context.trace_id == run.context.trace_id
    assert [event.name for event in run.events] == ["cognition.run.completed"]
    assert run.attributes is not None
    assert run.attributes["session.id"] == "session-1"
    assert run.attributes["cognition.run.id"] == "run-1"
    assert run.attributes["gen_ai.conversation.id"] == "thread-1"


@pytest.mark.asyncio
async def test_agent_run_span_closes_from_copied_async_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("cognition.trace-stream.tests")
    monkeypatch.setattr("server.app.observability.get_tracer", lambda _name: tracer)

    async def stream() -> Any:
        with agent_run_span(
            session_id="session-stream",
            run_id="run-stream",
            thread_id="thread-stream",
        ) as run_span:
            yield run_span

    events = stream()
    assert await anext(events) is not None
    close_task = asyncio.create_task(events.aclose(), context=ContextVarsContext())
    await close_task

    provider.shutdown()
    assert [span.name for span in exporter.get_finished_spans()] == [
        "cognition.agent.run"
    ]


def test_scope_fingerprint_requires_operator_hmac_key() -> None:
    setup_tracing(enabled=False, observability_scope_hmac_key=None)
    assert _scope_fingerprint({"tenant": "acme"}) is None

    setup_tracing(enabled=False, observability_scope_hmac_key="operator-secret")
    first = _scope_fingerprint({"tenant": "acme", "project": "ios"})
    second = _scope_fingerprint({"project": "ios", "tenant": "acme"})

    assert first == second
    assert first is not None
    assert "acme" not in first
    assert "ios" not in first
    setup_tracing(enabled=False, observability_scope_hmac_key=None)


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
