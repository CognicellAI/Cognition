"""Validate private structured results through the real graph-stream adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.a2ui.core import (
    A2UI_EXTENSION_URI,
    A2UI_MEDIA_TYPE,
    BASIC_CATALOG_ID,
    A2UIInvocationContext,
    A2UIResponseEnvelope,
)
from server.app.agent.runtime import (
    ArtifactEvent,
    DeepAgentRuntime,
    DoneEvent,
    ErrorEvent,
    TokenEvent,
)
from server.app.llm.deep_agent_service import (
    DeepAgentStreamingService,
    _with_a2ui_output,
    _with_execution_timeout,
)
from tests.unit.test_streaming_bugs import _make_run, _make_session, _make_settings


def result(catalog=BASIC_CATALOG_ID):
    return {
        "text": "Ready",
        "messages": [
            {
                "version": "v1.0",
                "createSurface": {
                    "surfaceId": "main",
                    "catalogId": catalog,
                    "components": [{"id": "root", "component": "Text", "text": "Ready"}],
                },
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_first", [False, True])
async def test_initial_and_approval_resume_use_private_validated_output(interrupt_first):
    class Graph:
        async def astream(self, *args, **kwargs):
            yield {
                "type": "updates",
                "ns": (),
                "data": {"model": {"structured_response": result()}},
            }

    session = _make_session()
    run = _make_run(
        session, {"a2ui": {"extension_uri": A2UI_EXTENSION_URI, "catalog_ids": [BASIC_CATALOG_ID]}}
    )
    storage = MagicMock()
    storage.get_session = AsyncMock(return_value=session)
    storage.get_run = AsyncMock(return_value=run)
    storage.get_active_run = AsyncMock(return_value=run)
    storage.get_checkpointer = AsyncMock(return_value=MagicMock())
    storage.get_store = AsyncMock(return_value=MagicMock())
    graph = Graph()
    if interrupt_first:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt

        def approval(state):
            interrupt({"action_requests": [{"name": "example", "args": {}, "id": "approval-1"}]})
            return {"structured_response": result()}

        builder = StateGraph(dict)
        builder.add_node("approval", approval)
        builder.add_edge(START, "approval")
        builder.add_edge("approval", END)
        graph = builder.compile(checkpointer=InMemorySaver())
    factory = AsyncMock(return_value=SimpleNamespace(agent=graph, sandbox_backend=None))
    with patch("server.app.storage.factory.create_storage_backend", return_value=storage):
        service = DeepAgentStreamingService(_make_settings())
    service.storage_backend = storage
    with (
        patch.object(
            service, "_resolve_model", AsyncMock(return_value=(MagicMock(), "mock", "mock", 100))
        ),
        patch("server.app.llm.deep_agent_service.create_cognition_agent", factory),
    ):
        initial = [
            e
            async for e in service.stream_response(
                session.id, session.thread_id, session.workspace_path, "Show status", run_id=run.id
            )
        ]
        resumed = [
            e
            async for e in service.resume_response(
                session.id, session.thread_id, session.workspace_path, "approve", "example"
            )
        ]
    if interrupt_first:
        from server.app.agent.runtime import InterruptEvent

        assert any(isinstance(e, InterruptEvent) for e in initial)
        assert not any(isinstance(e, ArtifactEvent) for e in initial)
    for events in (resumed,) if interrupt_first else (initial, resumed):
        assert not [e for e in events if isinstance(e, ErrorEvent)]
        artifacts = [e for e in events if isinstance(e, ArtifactEvent)]
        assert len(artifacts) == 1
        assert artifacts[0].media_type == A2UI_MEDIA_TYPE
        assert artifacts[0].value == result()["messages"]
        assert [e.content for e in events if isinstance(e, TokenEvent)] == ["Ready"]
    if not interrupt_first:
        assert initial[-1].__class__ is DoneEvent
    assert resumed[-1].__class__ is DoneEvent
    assert all(
        call.args[0].response_format is A2UIResponseEnvelope for call in factory.call_args_list
    )


@pytest.mark.asyncio
async def test_invalid_native_graph_result_never_leaks_as_json_artifact():
    class Graph:
        async def astream(self, *args, **kwargs):
            yield {
                "type": "updates",
                "ns": (),
                "data": {"model": {"structured_response": result("https://foreign.example")}},
            }

    runtime = DeepAgentRuntime(
        Graph(), MagicMock(), thread_id="ui", structured_response_as_artifact=False
    )
    context = A2UIInvocationContext(A2UI_EXTENSION_URI, (BASIC_CATALOG_ID,), {})
    events = [
        e
        async for e in _with_a2ui_output(
            runtime.astream_events("UI"), runtime, context, "ui", "run"
        )
    ]
    assert not [e for e in events if isinstance(e, ArtifactEvent)]
    assert [e.code for e in events if isinstance(e, ErrorEvent)] == ["A2UI_OUTPUT_INVALID"]


@pytest.mark.asyncio
async def test_output_repair_shares_the_original_timeout():
    class Graph:
        calls = 0

        async def astream(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(0.2)
            yield {
                "type": "updates",
                "ns": (),
                "data": {"model": {"structured_response": result("https://foreign.example")}},
            }

    graph = Graph()
    runtime = DeepAgentRuntime(
        graph, MagicMock(), thread_id="ui", structured_response_as_artifact=False
    )
    context = A2UIInvocationContext(A2UI_EXTENSION_URI, (BASIC_CATALOG_ID,), {})
    with pytest.raises(TimeoutError):
        async for _ in _with_execution_timeout(
            _with_a2ui_output(runtime.astream_events("UI"), runtime, context, "ui", "run"), 0.3
        ):
            pass
    assert graph.calls == 2
