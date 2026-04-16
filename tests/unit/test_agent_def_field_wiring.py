"""Unit tests for AgentDefinition field wiring in DeepAgentStreamingService.

Verifies that ALL AgentDefinition fields are consumed at runtime — not just
system_prompt, skills, and subagents (the original 3), but also:
  - memory → create_cognition_agent(memory=...)
  - interrupt_on → create_cognition_agent(interrupt_on=...)
  - middleware → resolved and passed to create_cognition_agent(middleware=...)
  - tools → resolved and merged with runtime-resolved tools
  - config.model / config.provider → overrides global provider default
  - config.recursion_limit → overrides default recursion limit
  - config.temperature → passed to _build_model
  - config.max_tokens → passed to _build_model

These tests exercise the "Code Path A" service layer
(deep_agent_service.py:stream_response) which was previously only consuming 3
of 12+ fields.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.runtime import DoneEvent, TokenEvent
from server.app.models import Session, SessionConfig, SessionStatus


def _get_params(mock: MagicMock) -> Any:
    """Extract CognitionAgentParams from a mocked create_cognition_agent call."""
    call = mock.call_args
    return call.kwargs.get("params") or (call.args[0] if call.args else None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    agent_name: str = "test-agent",
    provider: str = "mock",
    model: str = "mock-model",
) -> Session:
    return Session(
        id="sess-wire-test",
        workspace_path="/tmp/ws",
        title="Wire Test",
        thread_id="thread-wire-test",
        status=SessionStatus.ACTIVE,
        config=SessionConfig(
            provider=cast(Any, provider),
            model=model,
        ),
        agent_name=agent_name,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


async def _events(*events: Any) -> AsyncGenerator[Any, None]:
    for event in events:
        yield event


def _make_mock_runtime(*events: Any) -> MagicMock:
    mock = MagicMock()
    mock.astream_events = MagicMock(return_value=_events(*events))
    return mock


def _base_patches(mock_runtime: MagicMock, session: Session) -> tuple:
    """Standard patches needed to isolate stream_response from real infra."""
    mock_storage = MagicMock()
    mock_storage.get_session = AsyncMock(return_value=session)
    mock_storage.get_checkpointer = AsyncMock(return_value=MagicMock())

    return (
        patch(
            "server.app.llm.deep_agent_service.DeepAgentRuntime",
            return_value=mock_runtime,
        ),
        patch(
            "server.app.llm.deep_agent_service.DeepAgentStreamingService._resolve_model",
            new_callable=AsyncMock,
            return_value=(MagicMock(), "mock", "mock-model", 100),
        ),
        patch(
            "server.app.llm.deep_agent_service.create_cognition_agent",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "server.app.storage.factory.create_storage_backend",
            return_value=mock_storage,
        ),
    )


async def _run(
    patches: tuple,
    session: Session,
    mock_def_registry: Any = None,
) -> tuple[list[Any], MagicMock]:
    """Drive stream_response and return (events, create_cognition_agent mock)."""
    from server.app.llm.deep_agent_service import DeepAgentStreamingService
    from server.app.settings import Settings

    s = MagicMock(spec=Settings)
    s.trusted_tool_namespaces = ["server.app.tools"]
    service = DeepAgentStreamingService(s)
    mock_storage = MagicMock()
    mock_storage.get_session = AsyncMock(return_value=session)
    mock_storage.get_checkpointer = AsyncMock(return_value=MagicMock())
    mock_storage.get_store = AsyncMock(return_value=MagicMock())
    service.storage_backend = mock_storage

    p1, p2, p3, p4 = patches

    mock_config_store = MagicMock()
    mock_config_store.get_agent_definition = AsyncMock(return_value=None)
    mock_config_store.list_agent_definitions = AsyncMock(return_value=[])
    mock_config_store.list_tools = AsyncMock(return_value=[])
    mock_config_store.list_mcp_servers = AsyncMock(return_value=[])

    service._config_store = mock_config_store

    with p1, p2, p3 as create_agent_mock, p4:
        if mock_def_registry is not None:
            mock_config_store.get_agent_definition = AsyncMock(
                side_effect=lambda name, scope=None: mock_def_registry.get(name)
            )
            mock_config_store.list_agent_definitions = AsyncMock(
                return_value=mock_def_registry.subagents()
                if hasattr(mock_def_registry, "subagents")
                else []
            )

        collected = []
        async for event in service.stream_response(
            session_id=session.id,
            thread_id=session.thread_id,
            project_path="/tmp/ws",
            content="hello",
        ):
            collected.append(event)

    return collected, create_agent_mock


# ---------------------------------------------------------------------------
# Baseline: no agent_def → falls back gracefully
# ---------------------------------------------------------------------------


class TestNoAgentDef:
    @pytest.mark.asyncio
    async def test_stream_works_without_agent_def(self):
        """Service must work when no AgentDefinition is found."""
        session = _make_session()
        mock_runtime = _make_mock_runtime(TokenEvent(content="hi"), DoneEvent())
        patches = _base_patches(mock_runtime, session)

        # def_registry returns None for any agent name
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=None)
        mock_def_registry.subagents = MagicMock(return_value=[])

        events, _ = await _run(patches, session, mock_def_registry=mock_def_registry)
        assert any(isinstance(e, DoneEvent) for e in events)


# ---------------------------------------------------------------------------
# memory wiring
# ---------------------------------------------------------------------------


class TestMemoryWiring:
    @pytest.mark.asyncio
    async def test_agent_def_memory_passed_to_create_cognition_agent(self):
        """memory from AgentDefinition must be forwarded to create_cognition_agent."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            memory=["AGENTS.md", ".cognition/memory/context.md"],
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.memory == ["AGENTS.md", ".cognition/memory/context.md"]

    @pytest.mark.asyncio
    async def test_empty_memory_not_passed(self):
        """Empty memory list → memory=None passed to create_cognition_agent."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(name="test-agent", system_prompt="test", memory=[])
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.memory is None


# ---------------------------------------------------------------------------
# interrupt_on wiring
# ---------------------------------------------------------------------------


class TestInterruptOnWiring:
    @pytest.mark.asyncio
    async def test_interrupt_on_passed_to_create_cognition_agent(self):
        """interrupt_on from AgentDefinition must be forwarded."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            interrupt_on={"execute": True, "write_file": False},
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.interrupt_on == {"execute": True, "write_file": False}

    @pytest.mark.asyncio
    async def test_empty_interrupt_on_not_passed(self):
        """Empty interrupt_on → interrupt_on=None passed."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(name="test-agent", system_prompt="test", interrupt_on={})
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.interrupt_on is None


class TestStructuredOutputAndContextControls:
    @pytest.mark.asyncio
    async def test_response_format_passed_to_create_cognition_agent(self):
        """response_format from AgentDefinition must be forwarded."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            response_format="tests.fixtures.schemas.CodeReviewResult",
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.response_format == "tests.fixtures.schemas.CodeReviewResult"

    @pytest.mark.asyncio
    async def test_session_response_format_overrides_agent_definition(self):
        """SessionConfig.response_format must take precedence over AgentDefinition."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        session.config.response_format = "tests.fixtures.schemas.SessionResult"
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            response_format="tests.fixtures.schemas.AgentResult",
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.response_format == "tests.fixtures.schemas.SessionResult"

    @pytest.mark.asyncio
    async def test_tool_token_limit_before_evict_passed_to_create_cognition_agent(self):
        """tool_token_limit_before_evict from AgentConfig must be forwarded."""
        from server.app.agent.definition import AgentConfig, AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            config=AgentConfig(tool_token_limit_before_evict=12345),
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.tool_token_limit_before_evict == 12345


# ---------------------------------------------------------------------------
# middleware wiring
# ---------------------------------------------------------------------------


class TestMiddlewareWiring:
    @pytest.mark.asyncio
    async def test_middleware_resolved_and_passed(self):
        """Declarative middleware specs must be resolved and passed to create_cognition_agent."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            middleware=[{"name": "tool_retry", "max_retries": 2}],
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        # Mock _resolve_middleware to return a sentinel middleware instance
        sentinel_mw = MagicMock(name="ToolRetryMiddleware")
        with patch(
            "server.app.llm.deep_agent_service._resolve_middleware",
            return_value=[sentinel_mw],
        ):
            _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.middleware == [sentinel_mw]

    @pytest.mark.asyncio
    async def test_empty_middleware_not_passed(self):
        """Empty middleware list → middleware=None."""
        from server.app.agent.definition import AgentDefinition

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())
        patches = _base_patches(mock_runtime, session)

        agent_def = AgentDefinition(name="test-agent", system_prompt="test", middleware=[])
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        _, create_agent_mock = await _run(patches, session, mock_def_registry=mock_def_registry)

        params = _get_params(create_agent_mock)
        assert params is not None
        assert params.middleware is None


# ---------------------------------------------------------------------------
# tools wiring
# ---------------------------------------------------------------------------


class TestToolsWiring:
    @pytest.mark.asyncio
    async def test_agent_def_tools_added_to_runtime_tools(self):
        """Tools from AgentDefinition._resolve_tools() are included in runtime tools."""
        from langchain_core.tools import BaseTool

        from server.app.agent.definition import AgentDefinition
        from server.app.llm.deep_agent_service import DeepAgentStreamingService
        from server.app.settings import Settings

        session = _make_session()
        mock_runtime = _make_mock_runtime(DoneEvent())

        agent_def_tool = MagicMock(spec=BaseTool)
        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            tools=["server.app.tools.some_tool"],
        )
        mock_def_registry = MagicMock()
        mock_def_registry.get = MagicMock(return_value=agent_def)
        mock_def_registry.subagents = MagicMock(return_value=[])

        s = MagicMock(spec=Settings)
        service = DeepAgentStreamingService(s)

        mock_config_store = MagicMock()
        mock_config_store.get_agent_definition = AsyncMock(return_value=agent_def)
        mock_config_store.list_agent_definitions = AsyncMock(return_value=[])
        mock_config_store.list_tools = AsyncMock(return_value=[])
        mock_config_store.list_mcp_servers = AsyncMock(return_value=[])
        service._config_store = mock_config_store

        mock_storage = MagicMock()
        mock_storage.get_session = AsyncMock(return_value=session)
        mock_storage.get_checkpointer = AsyncMock(return_value=MagicMock())
        mock_storage.get_store = AsyncMock(return_value=MagicMock())
        service.storage_backend = mock_storage

        resolve_tools_calls: list[Any] = []

        def _fake_resolve_tools(self_inner: Any, **kwargs: Any) -> list[Any]:
            resolve_tools_calls.append(kwargs)
            return [agent_def_tool]

        with (
            patch(
                "server.app.llm.deep_agent_service.DeepAgentRuntime",
                return_value=mock_runtime,
            ),
            patch.object(
                service,
                "_resolve_model",
                new_callable=AsyncMock,
                return_value=(MagicMock(), "mock", "mock-model", 100),
            ),
            patch(
                "server.app.llm.deep_agent_service.create_cognition_agent",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ) as create_agent_mock,
            patch(
                "server.app.storage.factory.create_storage_backend",
                return_value=mock_storage,
            ),
            patch(
                "server.app.agent.definition.AgentDefinition._resolve_tools",
                _fake_resolve_tools,
            ),
        ):
            async for _ in service.stream_response(
                session_id=session.id,
                thread_id=session.thread_id,
                project_path="/tmp/ws",
                content="hello",
            ):
                pass

        assert len(resolve_tools_calls) == 1

        params = _get_params(create_agent_mock)
        assert params is not None
        passed_tools = params.tools or []
        assert agent_def_tool in passed_tools

    @pytest.mark.asyncio
    async def test_session_recursion_limit_beats_agent_def(self):
        """session.config.recursion_limit must override agent_def.config.recursion_limit."""
        from server.app.agent.definition import AgentConfig, AgentDefinition
        from server.app.agent.resolver import RuntimeResolver

        session = _make_session()
        session.config.recursion_limit = 999

        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            config=AgentConfig(recursion_limit=42),
        )

        mock_provider = MagicMock()
        mock_provider.provider = "mock"
        mock_provider.model = "mock-model"
        mock_provider.enabled = True
        mock_provider.priority = 1
        mock_provider.api_key_env = None
        mock_provider.base_url = None
        mock_provider.region = None
        mock_provider.role_arn = None

        mock_config_store = MagicMock()
        mock_config_store.list_providers = AsyncMock(return_value=[mock_provider])

        resolver = RuntimeResolver(config_store=mock_config_store, settings=MagicMock())
        result = await resolver.resolve_model_config_for_session(
            session=session, scope=None, agent_def=agent_def
        )

        assert result.recursion_limit == 999


class TestConfigMaxTokens:
    @pytest.mark.asyncio
    async def test_resolve_model_passes_agent_max_tokens_to_build_model(self):
        """agent_def.config.max_tokens must be forwarded to RuntimeResolver.build_model."""
        from server.app.agent.definition import AgentConfig, AgentDefinition
        from server.app.agent.resolver import RuntimeResolver

        settings = MagicMock()
        resolver = RuntimeResolver(config_store=None, settings=settings)
        session = _make_session()
        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            config=AgentConfig(temperature=0.2, max_tokens=16000),
        )

        with (
            patch.object(
                resolver,
                "select_model_target_for_session",
                new=AsyncMock(
                    return_value=MagicMock(
                        provider="openai",
                        model_id="gpt-4o",
                        api_key_env=None,
                        base_url=None,
                        region=None,
                        role_arn=None,
                        max_retries=2,
                        timeout=30,
                    )
                ),
            ),
            patch.object(resolver, "build_model", return_value=MagicMock()),
            patch.object(resolver, "_warn_if_no_tool_call_support", new=AsyncMock()),
        ):
            await resolver.resolve_model_for_session(
                session=session, scope=None, agent_def=agent_def
            )
            kwargs = resolver.build_model.call_args.kwargs

        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 16000

    def test_build_bedrock_model_passes_top_level_max_tokens(self):
        """Bedrock max_tokens should use the dedicated top-level ChatBedrock kwarg."""
        from server.app.agent.resolver import RuntimeResolver

        settings = MagicMock()
        settings.aws_region = "us-east-1"
        settings.bedrock_role_arn = None
        settings.aws_access_key_id = None
        settings.aws_secret_access_key = None
        settings.aws_session_token = None
        resolver = RuntimeResolver(config_store=None, settings=settings)

        with patch("langchain_aws.ChatBedrock", return_value=MagicMock()) as chat_bedrock:
            resolver._build_bedrock_model(
                model_id="anthropic.claude-sonnet-4",
                region=None,
                role_arn=None,
                temperature=0.2,
                max_tokens=16000,
                max_retries=2,
                timeout=30,
            )

        kwargs = chat_bedrock.call_args.kwargs
        assert kwargs["model_kwargs"]["temperature"] == 0.2
        assert kwargs["max_tokens"] == 16000
        assert "max_tokens" not in kwargs["model_kwargs"]

    def test_openai_compatible_does_not_set_default_max_tokens(self):
        """OpenAI-compatible models should only receive max_tokens when explicitly set."""
        from server.app.agent.resolver import RuntimeResolver

        settings = MagicMock()
        settings.openai_compatible_api_key.get_secret_value.return_value = "token"
        settings.openai_compatible_base_url = "https://example.com/v1"
        resolver = RuntimeResolver(config_store=None, settings=settings)

        with patch(
            "server.app.agent.resolver.init_chat_model", return_value=MagicMock()
        ) as init_model:
            resolver.build_model(
                provider="openai_compatible",
                model_id="kimi-k2.5",
                api_key=None,
                base_url=None,
            )

        kwargs = init_model.call_args.kwargs
        assert "max_tokens" not in kwargs
