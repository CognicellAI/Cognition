"""Regression test for Cognition's native LangChain OpenTelemetry topology."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_native_langchain_tree_shares_agent_trace_and_exports_metrics() -> None:
    """The semantic bridge and metrics adapter must keep their roles separate."""
    script = textwrap.dedent(
        """
        import asyncio

        from opentelemetry import trace
        from opentelemetry import context as context_api
        from opentelemetry.instrumentation.utils import (
            _SUPPRESS_INSTRUMENTATION_KEY,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from server.app.observability import (
            CuratingSpanProcessor,
            _enable_langsmith_otel_bridge,
            _instrument_langchain_metrics,
            agent_run_span,
            langchain_metrics_callbacks,
        )

        span_exporter = InMemorySpanExporter()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(
            CuratingSpanProcessor(
                SimpleSpanProcessor(span_exporter),
                trace_detail="standard",
            )
        )
        trace.set_tracer_provider(tracer_provider)

        metric_reader = InMemoryMetricReader()
        meter_provider = MeterProvider(metric_readers=[metric_reader])
        _enable_langsmith_otel_bridge()
        _instrument_langchain_metrics(tracer_provider, meter_provider)

        from langchain_core.language_models.fake_chat_models import (
            FakeMessagesListChatModel,
        )
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.tracers.langchain import wait_for_all_tracers
        from langgraph.graph import END, START, StateGraph
        from typing_extensions import TypedDict

        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="ok",
                    usage_metadata={
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                )
            ]
        )

        class State(TypedDict):
            messages: list

        async def model_node(state):
            response = await model.ainvoke(state["messages"])
            return {"messages": [*state["messages"], response]}

        builder = StateGraph(State)
        builder.add_node("model", model_node)
        builder.add_edge(START, "model")
        builder.add_edge("model", END)
        graph = builder.compile()

        async def run():
            with agent_run_span(
                session_id="session-1",
                run_id="run-1",
                thread_id="thread-1",
            ):
                suppression_token = context_api.attach(
                    context_api.set_value(
                        _SUPPRESS_INSTRUMENTATION_KEY,
                        True,
                    )
                )
                try:
                    await graph.ainvoke(
                        {"messages": [HumanMessage(content="hello")]},
                        config={
                            "configurable": {"thread_id": "thread-1"},
                            "run_name": "native-langgraph",
                            "callbacks": langchain_metrics_callbacks(),
                        },
                    )
                finally:
                    context_api.detach(suppression_token)

        asyncio.run(run())
        wait_for_all_tracers()
        tracer_provider.force_flush()
        meter_provider.force_flush()

        spans = span_exporter.get_finished_spans()
        assert spans
        assert len({span.context.trace_id for span in spans}) == 1
        assert "cognition.agent.run" in {span.name for span in spans}
        assert "native-langgraph" in {span.name for span in spans}
        assert "model" in {span.name for span in spans}
        root = next(span for span in spans if span.name == "cognition.agent.run")
        graph_span = next(span for span in spans if span.name == "native-langgraph")
        assert graph_span.parent is not None
        assert graph_span.parent.span_id == root.context.span_id
        assert any(
            span.instrumentation_scope.name == "langsmith"
            for span in spans
        )
        assert all(
            span.instrumentation_scope.name
            != "opentelemetry.instrumentation.langchain"
            for span in spans
        )

        metrics = metric_reader.get_metrics_data()
        token_points = []
        for resource_metrics in metrics.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    if metric.name == "gen_ai.client.token.usage":
                        token_points.extend(metric.data.data_points)
        assert token_points
        assert sum(point.sum for point in token_points) == 5
        assert {
            point.attributes["gen_ai.token.type"]
            for point in token_points
        } == {"input", "output"}
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
