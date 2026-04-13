from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessageChunk

from server.app.agent.cognition_agent import _resolve_response_format
from server.app.agent.runtime import (
    DeepAgentRuntime,
    PlanningEvent,
    StepCompleteEvent,
    _resolve_middleware,
)
from server.app.llm.deep_agent_service import _build_model
from server.app.settings import Settings


class TestResponseFormatResolution:
    def test_resolves_dotted_path_to_class(self) -> None:
        resolved = _resolve_response_format("tests.fixtures.schemas.CodeReviewResult")
        assert resolved is not None
        assert resolved.__name__ == "CodeReviewResult"


class TestMiddlewareResolution:
    def test_resolves_summarization_tool_named_middleware(self) -> None:
        sentinel = object()
        with patch(
            "deepagents.middleware.summarization.create_summarization_tool_middleware",
            return_value=sentinel,
        ) as factory:
            result = _resolve_middleware({"name": "summarization_tool", "model": "gpt-4o"})

        assert result is sentinel
        factory.assert_called_once_with(model="gpt-4o")


class TestTodoStreamingTranslation:
    @pytest.mark.asyncio
    async def test_updates_emit_planning_and_step_complete_events(self) -> None:
        agent = MagicMock()

        async def _astream(*args, **kwargs):
            yield {
                "type": "updates",
                "ns": (),
                "data": {
                    "write_todos": {
                        "todos": [
                            {"content": "research", "status": "pending"},
                            {"content": "implement", "status": "pending"},
                        ]
                    }
                },
            }
            yield {
                "type": "updates",
                "ns": (),
                "data": {
                    "write_todos": {
                        "todos": [
                            {"content": "research", "status": "completed"},
                            {"content": "implement", "status": "pending"},
                        ]
                    }
                },
            }

        agent.astream = _astream
        runtime = DeepAgentRuntime(agent=agent, checkpointer=MagicMock(), thread_id="thread-1")

        events = [event async for event in runtime.astream_events("hello", thread_id="thread-1")]

        assert any(isinstance(event, PlanningEvent) for event in events)
        assert any(isinstance(event, StepCompleteEvent) for event in events)


class TestProviderModelPlumbing:
    def test_build_model_forwards_timeout_and_max_retries(self) -> None:
        settings = MagicMock(spec=Settings)
        settings.openai_api_key = None
        settings.openai_api_base = None
        settings.openai_compatible_api_key = MagicMock()
        settings.openai_compatible_api_key.get_secret_value.return_value = "test-key"
        settings.openai_compatible_base_url = "https://example.com/v1"
        settings.aws_region = "us-east-1"
        settings.aws_access_key_id = None
        settings.aws_access_key = None
        settings.aws_secret_access_key = None
        settings.aws_session_token = None
        settings.bedrock_role_arn = None

        with patch(
            "server.app.agent.resolver.init_chat_model", return_value=MagicMock()
        ) as init_model:
            _build_model(
                provider="openai",
                model_id="gpt-4o",
                api_key="key",
                base_url=None,
                region=None,
                role_arn=None,
                settings=settings,
                temperature=0.1,
                max_retries=5,
                timeout=60,
            )

        _, kwargs = init_model.call_args
        assert kwargs["max_retries"] == 5
        assert kwargs["timeout"] == 60


class TestRuntimeResume:
    @pytest.mark.asyncio
    async def test_resume_uses_langgraph_command(self) -> None:
        agent = MagicMock()
        agent.ainvoke = AsyncMock(return_value={"ok": True})
        runtime = DeepAgentRuntime(agent=agent, checkpointer=MagicMock(), thread_id="thread-1")

        result = await runtime.resume(
            decision="approve",
            tool_name="write_file",
            thread_id="thread-1",
        )

        assert result == {"ok": True}
        command = agent.ainvoke.await_args.args[0]
        assert command.resume == {"decisions": [{"type": "approve"}]}

    @pytest.mark.asyncio
    async def test_astream_resume_events_uses_command_resume(self) -> None:
        agent = MagicMock()

        async def _astream(*args, **kwargs):
            yield ((AIMessageChunk(content="ok"), {}), {})

        agent.astream = _astream
        runtime = DeepAgentRuntime(agent=agent, checkpointer=MagicMock(), thread_id="thread-1")

        events = [
            event
            async for event in runtime.astream_resume_events(
                decision="approve",
                tool_name="write_file",
                thread_id="thread-1",
            )
        ]

        assert events
