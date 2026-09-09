"""Behavioral checks for content-free timing and independent metrics export."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from server.app.telemetry import operation


@pytest.fixture
def telemetry(monkeypatch):
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(metrics, "get_meter", meter_provider.get_meter)
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    yield reader, exporter, provider
    meter_provider.shutdown()
    provider.shutdown()


@pytest.mark.asyncio
async def test_thread_timing_preserves_parent_and_omits_exception_content(telemetry):
    reader, exporter, provider = telemetry

    def work():
        with operation("cognition.publication.download"):
            raise ValueError("secret signed URL must not be exported")

    with provider.get_tracer("test").start_as_current_span("run") as root:
        with pytest.raises(ValueError, match="secret"):
            await asyncio.to_thread(work)
    child = next(s for s in exporter.get_finished_spans() if s.name.endswith("download"))
    assert child.parent.span_id == root.get_span_context().span_id
    assert child.status.status_code.name == "ERROR"
    assert not child.events
    assert "secret" not in repr(child.attributes)
    data = reader.get_metrics_data()
    point = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    assert dict(point.attributes) == {
        "operation": "cognition.publication.download",
        "outcome": "error",
    }
    assert point.count == 1 and point.sum >= 0


def test_instrumentation_failure_does_not_change_operation(monkeypatch):
    monkeypatch.setattr(trace, "get_tracer", MagicMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(metrics, "get_meter", MagicMock(side_effect=RuntimeError("offline")))
    with operation("cognition.publication.hash"):
        result = 42
    assert result == 42
    with pytest.raises(ValueError, match="original"):
        with operation("cognition.publication.hash"):
            raise ValueError("original")


def test_metrics_initialize_without_semantic_tracing(monkeypatch):
    from server.app import observability as obs

    reader = InMemoryMetricReader()
    captured = []
    monkeypatch.setattr(obs, "_create_metric_exporter", lambda endpoint: object())
    monkeypatch.setattr(obs, "PeriodicExportingMetricReader", lambda *a, **kw: reader)
    monkeypatch.setattr(metrics, "set_meter_provider", captured.append)
    install_trace = MagicMock()
    bridge = MagicMock()
    monkeypatch.setattr(trace, "set_tracer_provider", install_trace)
    monkeypatch.setattr(obs, "_enable_langsmith_otel_bridge", bridge)
    adapter = MagicMock()
    monkeypatch.setattr(obs, "_instrument_langchain_metrics", adapter)
    obs.setup_tracing(enabled=False, endpoint="http://collector:4318", metrics_enabled=True)
    assert len(captured) == 1
    install_trace.assert_not_called()
    bridge.assert_not_called()
    assert isinstance(adapter.call_args.args[0], trace.NoOpTracerProvider)
    captured[0].get_meter("test").create_histogram("latency", unit="s").record(0.25)
    assert reader.get_metrics_data().resource_metrics
    captured[0].shutdown()


def test_legacy_latency_mirrors_once_to_otlp(telemetry):
    from server.app.observability._latency import LatencyHistogram

    reader, _, _ = telemetry
    legacy = MagicMock()
    instrument = LatencyHistogram(legacy, "cognition.runtime.task.duration")
    instrument.labels(transport="a2a", outcome="success").observe(1.5)
    legacy.labels.return_value.observe.assert_called_once_with(1.5)
    point = (
        reader.get_metrics_data()
        .resource_metrics[0]
        .scope_metrics[0]
        .metrics[0]
        .data.data_points[0]
    )
    assert point.count == 1 and point.sum == 1.5


@pytest.mark.asyncio
async def test_synthetic_framework_context_does_not_fragment_operations(telemetry):
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    from server.app.telemetry import operation_context, operation_parent

    _, exporter, provider = telemetry
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("run") as root, operation_parent(root):
        foreign = NonRecordingSpan(SpanContext(123, 456, False, TraceFlags(1)))
        with trace.use_span(foreign):
            with operation("cognition.publication.upload"):
                pass

            @operation_context()
            def backend():
                with tracer.start_as_current_span("sdk.execute"):
                    pass

            await asyncio.to_thread(backend)
        with tracer.start_as_current_span("native.tool") as tool:
            with operation("cognition.publication.hash"):
                pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert {s.context.trace_id for s in spans.values()} == {root.get_span_context().trace_id}
    assert spans["cognition.publication.upload"].parent.span_id == root.get_span_context().span_id
    assert spans["sdk.execute"].parent.span_id == root.get_span_context().span_id
    assert spans["cognition.publication.hash"].parent.span_id == tool.get_span_context().span_id


def test_streaming_first_chunk_is_once_per_call_without_traces():
    import subprocess
    import sys
    import textwrap

    subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
        import asyncio
        from opentelemetry import trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from server.app.observability import _instrument_langchain_metrics, langchain_metrics_callbacks
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        _instrument_langchain_metrics(trace.NoOpTracerProvider(), provider)
        async def run():
            model = FakeListChatModel(responses=['hello', 'world'])
            for _ in range(2):
                chunks = [c async for c in model.astream('hi', config={'callbacks': langchain_metrics_callbacks()})]
                assert chunks
        asyncio.run(run())
        metrics = [m for r in reader.get_metrics_data().resource_metrics for s in r.scope_metrics for m in s.metrics]
        first = next(m for m in metrics if m.name == 'cognition.model.time_to_first_chunk')
        assert sum(p.count for p in first.data.data_points) == 2
        assert all(not p.attributes for p in first.data.data_points)
        provider.shutdown()
    """),
        ],
        check=True,
        timeout=30,
    )


def test_bounded_export_queue_does_not_block_application():
    import threading

    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

    entered = threading.Event()
    release = threading.Event()

    class BlockedExporter(SpanExporter):
        def export(self, spans):
            entered.set()
            release.wait(5)
            return SpanExportResult.FAILURE

        def shutdown(self):
            pass

    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(
            BlockedExporter(), max_queue_size=4, max_export_batch_size=1, schedule_delay_millis=1
        )
    )
    tracer = provider.get_tracer("queue-test")
    try:
        with tracer.start_as_current_span("first"):
            pass
        assert entered.wait(2)
        # An exporter remains blocked while far more spans than the queue can hold finish.
        completed = threading.Event()

        def work():
            for _ in range(100):
                with tracer.start_as_current_span("operation"):
                    pass
            completed.set()

        worker = threading.Thread(target=work)
        worker.start()
        assert completed.wait(2), "Agent work blocked behind the telemetry sink"
        worker.join(2)
    finally:
        release.set()
        provider.shutdown()


def test_lifespan_instrumentation_accepts_ingress_trace_context():
    """Startup must instrument the first HTTP request, not only a future rebuild."""
    import subprocess
    import sys

    code = """
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from server.app.observability import setup_tracing, agent_run_span
exporter = InMemorySpanExporter()
@asynccontextmanager
async def lifespan(app):
    setup_tracing(app=app, endpoint=None, metrics_enabled=False)
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield
app = FastAPI(lifespan=lifespan)
@app.get('/work')
async def work():
    ingress = trace.get_current_span().get_span_context()
    return {'trace_id': format(ingress.trace_id, '032x')}
with TestClient(app) as client:
    response = client.get('/work', headers={'traceparent': '00-11111111111111111111111111111111-2222222222222222-01'})
assert response.json()['trace_id'] == '11111111111111111111111111111111'
spans = [s for s in exporter.get_finished_spans() if s.kind.name == 'SERVER']
assert len(spans) == 1
assert spans[0].parent.span_id == int('2222222222222222', 16)
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, timeout=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_native_checkpoint_timings_preserve_thread_state_and_instance(
    telemetry, tmp_path, backend
):
    from langgraph.graph import StateGraph
    from typing_extensions import TypedDict

    from server.app.storage._checkpoint_telemetry import observe_checkpointer
    from server.app.storage.memory import MemoryStorageBackend
    from server.app.storage.sqlite import SqliteStorageBackend

    class State(TypedDict):
        value: int

    storage = (
        MemoryStorageBackend(str(tmp_path))
        if backend == "memory"
        else SqliteStorageBackend(str(tmp_path / "state.db"), str(tmp_path))
    )
    saver = await storage.get_checkpointer()
    try:
        assert observe_checkpointer(saver) is saver
        original = saver.aget_tuple
        assert observe_checkpointer(saver).aget_tuple is original
        assert await storage.get_checkpointer() is saver
        graph = StateGraph(State)
        graph.add_node("work", lambda state: {"value": state["value"] + 1})
        graph.set_entry_point("work")
        graph.set_finish_point("work")
        runtime = graph.compile(checkpointer=saver)
        first = {"configurable": {"thread_id": "first"}}
        second = {"configurable": {"thread_id": "second"}}
        await runtime.ainvoke({"value": 1}, first)
        await runtime.ainvoke({"value": 20}, second)
        assert (await runtime.aget_state(first)).values == {"value": 2}
        assert (await runtime.aget_state(second)).values == {"value": 21}
        names = {s.name for s in telemetry[1].get_finished_spans()}
        assert {
            "cognition.checkpoint.load",
            "cognition.checkpoint.save",
            "cognition.checkpoint.write_pending",
        } <= names
    finally:
        await storage.close_checkpointer()
