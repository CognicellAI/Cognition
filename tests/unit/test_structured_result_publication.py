"""Exercise structured output through the production graph-stream adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from server.app.agent.runtime import ArtifactEvent, DeepAgentRuntime


class Report(BaseModel):
    total: int


@pytest.mark.asyncio
async def test_validated_root_result_becomes_data_artifact() -> None:
    class Graph:
        async def astream(self, *args, **kwargs):
            yield {
                "type": "updates",
                "ns": (),
                "data": {"model": {"structured_response": Report(total=42)}},
            }

    runtime = DeepAgentRuntime(Graph(), MagicMock(), thread_id="report-thread")
    events = [event async for event in runtime.astream_events("report")]
    artifacts = [event for event in events if isinstance(event, ArtifactEvent)]
    assert len(artifacts) == 1
    assert artifacts[0].kind == "data"
    assert artifacts[0].value == {"total": 42}


@pytest.mark.asyncio
async def test_subagent_structured_results_are_not_root_deliverables() -> None:
    class Graph:
        async def astream(self, *args, **kwargs):
            yield {
                "type": "updates",
                "ns": ("tools:child",),
                "data": {"model": {"structured_response": Report(total=42)}},
            }

    runtime = DeepAgentRuntime(Graph(), MagicMock(), thread_id="report-thread")
    events = [event async for event in runtime.astream_events("report")]
    assert not any(isinstance(event, ArtifactEvent) for event in events)


def test_bedrock_invoke_model_uses_validated_tool_strategy():
    from types import SimpleNamespace

    from langchain.agents.structured_output import ToolStrategy

    from server.app.agent.cognition_agent import _runtime_response_format

    result = _runtime_response_format(SimpleNamespace(_llm_type="amazon_bedrock_chat"), Report)
    assert isinstance(result, ToolStrategy)
    assert result.schema is Report
    assert _runtime_response_format(SimpleNamespace(_llm_type="other"), Report) is Report


@pytest.mark.asyncio
async def test_nonstreaming_model_message_preserves_text() -> None:
    from langchain_core.messages import AIMessage

    from server.app.agent.runtime import TokenEvent

    class Graph:
        async def astream(self, *args, **kwargs):
            yield {"type": "messages", "ns": (), "data": (AIMessage(content="Total: 60"), {})}

    runtime = DeepAgentRuntime(Graph(), MagicMock(), thread_id="report-thread")
    events = [event async for event in runtime.astream_events("report")]
    assert [event.content for event in events if isinstance(event, TokenEvent)] == ["Total: 60"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_root_update_preserves_text_without_duplicate_tokens(streamed):
    from langchain_core.messages import AIMessage, AIMessageChunk

    from server.app.agent.runtime import TokenEvent

    class Graph:
        async def astream(self, *args, **kwargs):
            if streamed:
                yield {
                    "type": "messages",
                    "ns": (),
                    "data": (AIMessageChunk(id="msg", content="Total: 60"), {}),
                }
            yield {
                "type": "updates",
                "ns": (),
                "data": {"model": {"messages": [AIMessage(id="msg", content="Total: 60")]}},
            }

    runtime = DeepAgentRuntime(Graph(), MagicMock(), thread_id="report-thread")
    events = [event async for event in runtime.astream_events("report")]
    assert [event.content for event in events if isinstance(event, TokenEvent)] == ["Total: 60"]


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [42, "invalid"])
async def test_actual_agent_validates_structured_output_before_publication(total):
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ToolStrategy
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from server.app.agent.runtime import ErrorEvent

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.messages import AIMessageChunk
            from langchain_core.outputs import ChatGenerationChunk

            message = next(self.messages)
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls)
            )

    model = Model(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "stats",
                            "name": "Report",
                            "args": {"total": total},
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )
    checkpointer = InMemorySaver()
    graph = create_agent(
        model=model,
        response_format=ToolStrategy(Report, handle_errors=False),
        checkpointer=checkpointer,
    )
    runtime = DeepAgentRuntime(graph, checkpointer, thread_id="validated-result")
    events = [event async for event in runtime.astream_events("Return statistics")]
    artifacts = [event for event in events if isinstance(event, ArtifactEvent)]
    if total == 42:
        assert len(artifacts) == 1
        assert artifacts[0].value == {"total": 42}
    else:
        assert artifacts == []
        assert any(isinstance(event, ErrorEvent) for event in events)
