"""Regression tests for three streaming bugs fixed in v0.2.1.

Bug 1 — done event fires twice:
    DeepAgentRuntime.astream_events() yields DoneEvent at the end of its
    generator. DeepAgentStreamingService was re-yielding it (pass-through)
    then emitting its own second DoneEvent. Callers received two done signals.

Bug 2 — content in done payload doubled:
    Both on_chat_model_stream AND on_chain_stream/model fired for every
    LangGraph streaming token, causing every token to be emitted twice as a
    TokenEvent. accumulated_content therefore contained the full response
    concatenated with itself.

Bug 3 — model in usage event reports gpt-4o regardless of provider:
    UsageEvent(model=llm_settings.llm_model) always read the generic
    llm_model field (default "gpt-4o"). Bedrock users set
    COGNITION_BEDROCK_MODEL_ID, not COGNITION_LLM_MODEL, so their actual
    model ID was never reflected in usage events.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.runtime import (
    DoneEvent,
    TokenEvent,
    UsageEvent,
)
from server.app.models import Session, SessionConfig, SessionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    provider: str = "mock",
    model: str = "mock-model",
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
) -> MagicMock:
    """Build a minimal mock Settings object."""
    from server.app.settings import Settings

    s = MagicMock(spec=Settings)
    s.llm_provider = provider
    s.llm_model = model
    s.bedrock_model_id = bedrock_model_id
    s.llm_max_tokens = None
    s.mcp_server_configs = []
    s.agent_recursion_limit = 100
    s.trusted_tool_namespaces = ["server.app.tools"]
    return s


def _make_session(
    provider: Literal["openai", "bedrock", "mock", "openai_compatible"] = "mock",
    model: str = "mock-model",
) -> Session:
    return Session(
        id="sess-stream-test",
        workspace_path="/tmp/ws",
        title="Stream Test",
        thread_id="thread-stream-test",
        status=SessionStatus.ACTIVE,
        config=SessionConfig(provider=provider, model=model),
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


async def _runtime_events(*events: Any) -> AsyncGenerator[Any, None]:
    """Async generator that yields the provided events, mimicking the runtime."""
    for event in events:
        yield event


def _make_mock_runtime(*events: Any) -> MagicMock:
    """Build a MagicMock DeepAgentRuntime that streams the given events."""
    mock_runtime = MagicMock()
    mock_runtime.astream_events = MagicMock(return_value=_runtime_events(*events))
    return mock_runtime


def _stream_patches(mock_runtime: MagicMock) -> tuple:
    """Return the standard set of patches needed to drive stream_response in isolation."""
    return (
        patch(
            "server.app.llm.deep_agent_service.DeepAgentRuntime",
            return_value=mock_runtime,
        ),
        patch(
            "server.app.llm.deep_agent_service.DeepAgentStreamingService._get_model",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "server.app.llm.deep_agent_service.create_cognition_agent",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "server.app.llm.deep_agent_service.create_storage_backend",
            return_value=MagicMock(
                get_checkpointer=AsyncMock(return_value=MagicMock()),
            ),
        ),
    )


async def _collect(service: Any, session: Session) -> list[Any]:
    """Drive stream_response and return all events."""
    collected: list[Any] = []
    async for event in service.stream_response(
        session_id=session.id,
        thread_id=session.thread_id,
        project_path=session.workspace_path,
        content="hello",
    ):
        collected.append(event)
    return collected


# ---------------------------------------------------------------------------
# Bug 1 — exactly one DoneEvent reaches the caller
# ---------------------------------------------------------------------------


class TestExactlyOneDoneEvent:
    """The service must emit exactly one DoneEvent regardless of what the runtime yields."""

    @pytest.mark.asyncio
    async def test_single_done_when_runtime_yields_done(self):
        """Runtime yields DoneEvent → service absorbs it, emits its own → caller gets one."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        mock_runtime = _make_mock_runtime(TokenEvent(content="hello"), DoneEvent())
        service = DeepAgentStreamingService(_make_settings())

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(service, _make_session())

        done_events = [e for e in collected if isinstance(e, DoneEvent)]
        assert len(done_events) == 1, (
            f"Expected exactly 1 DoneEvent, got {len(done_events)}. "
            f"Sequence: {[type(e).__name__ for e in collected]}"
        )

    @pytest.mark.asyncio
    async def test_single_done_when_runtime_yields_no_done(self):
        """Even if the runtime emits no DoneEvent, the service still emits exactly one."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        mock_runtime = _make_mock_runtime(TokenEvent(content="world"))
        service = DeepAgentStreamingService(_make_settings())

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(service, _make_session())

        done_events = [e for e in collected if isinstance(e, DoneEvent)]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_done_is_last_event(self):
        """DoneEvent must be the final event in the stream (after UsageEvent)."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        mock_runtime = _make_mock_runtime(TokenEvent(content="hi"), DoneEvent())
        service = DeepAgentStreamingService(_make_settings())

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(service, _make_session())

        assert isinstance(collected[-1], DoneEvent), (
            f"Last event should be DoneEvent, got {type(collected[-1]).__name__}"
        )


# ---------------------------------------------------------------------------
# Bug 2 — accumulated content is not doubled
# ---------------------------------------------------------------------------


class TestContentNotDoubled:
    """Tokens must be emitted exactly once; accumulated content must equal the response."""

    @pytest.mark.asyncio
    async def test_token_count_matches_runtime_output(self):
        """Each token from the runtime produces exactly one TokenEvent downstream."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        tokens = ["Hello", ", ", "world", "!"]
        mock_runtime = _make_mock_runtime(
            *[TokenEvent(content=t) for t in tokens],
            DoneEvent(),
        )
        service = DeepAgentStreamingService(_make_settings())

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(service, _make_session())

        token_events = [e for e in collected if isinstance(e, TokenEvent)]
        assert len(token_events) == len(tokens), (
            f"Expected {len(tokens)} TokenEvents, got {len(token_events)}"
        )
        accumulated = "".join(e.content for e in token_events)
        assert accumulated == "Hello, world!", (
            f"Content doubled or otherwise wrong: {accumulated!r}"
        )

    @pytest.mark.asyncio
    async def test_usage_event_output_tokens_not_doubled(self):
        """output_tokens in UsageEvent must count each token once, not twice."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        # Two single-word tokens → output_tokens should be 2, not 4
        mock_runtime = _make_mock_runtime(
            TokenEvent(content="Hello"),
            TokenEvent(content="World"),
            DoneEvent(),
        )
        service = DeepAgentStreamingService(_make_settings())

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(service, _make_session())

        usage_events = [e for e in collected if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].output_tokens == 2, (
            f"output_tokens should be 2 (one per token), got {usage_events[0].output_tokens}"
        )


# ---------------------------------------------------------------------------
# Bug 3 — model in UsageEvent reflects actual provider model, not gpt-4o default
# ---------------------------------------------------------------------------


class TestUsageEventModelField:
    """UsageEvent.model must report the actual model ID used, not the llm_model default."""

    @pytest.mark.asyncio
    async def test_bedrock_model_id_in_usage_event(self):
        """When provider=bedrock, UsageEvent.model must be bedrock_model_id, not llm_model."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        bedrock_model = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        settings = _make_settings(
            provider="bedrock",
            model="gpt-4o",  # the wrong default — must NOT appear in UsageEvent
            bedrock_model_id=bedrock_model,
        )
        mock_runtime = _make_mock_runtime(TokenEvent(content="hi"), DoneEvent())
        service = DeepAgentStreamingService(settings)

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(
                service, _make_session(provider="bedrock", model=bedrock_model)
            )

        usage_events = [e for e in collected if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].model == bedrock_model, (
            f"Expected model={bedrock_model!r}, got {usage_events[0].model!r}. "
            "Bedrock users set COGNITION_BEDROCK_MODEL_ID, not COGNITION_LLM_MODEL."
        )

    @pytest.mark.asyncio
    async def test_openai_model_id_in_usage_event(self):
        """When provider=openai, UsageEvent.model must be llm_model."""
        from server.app.llm.deep_agent_service import DeepAgentStreamingService

        settings = _make_settings(provider="openai", model="gpt-4o-mini")
        mock_runtime = _make_mock_runtime(TokenEvent(content="hi"), DoneEvent())
        service = DeepAgentStreamingService(settings)

        p1, p2, p3, p4 = _stream_patches(mock_runtime)
        with p1, p2, p3, p4:
            collected = await _collect(
                service, _make_session(provider="openai", model="gpt-4o-mini")
            )

        usage_events = [e for e in collected if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].model == "gpt-4o-mini"

    def test_get_model_id_bedrock(self):
        """_get_model_id() returns bedrock_model_id for bedrock provider."""
        from server.app.llm.provider_fallback import _get_model_id

        settings = _make_settings(
            provider="bedrock",
            model="gpt-4o",
            bedrock_model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        )
        assert _get_model_id(settings) == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_get_model_id_openai(self):
        """_get_model_id() returns llm_model for non-bedrock providers."""
        from server.app.llm.provider_fallback import _get_model_id

        settings = _make_settings(provider="openai", model="gpt-4o-mini")
        assert _get_model_id(settings) == "gpt-4o-mini"

    def test_get_model_id_openai_compatible(self):
        """_get_model_id() returns llm_model for openai_compatible provider."""
        from server.app.llm.provider_fallback import _get_model_id

        settings = _make_settings(provider="openai_compatible", model="meta-llama/llama-3.3-70b")
        assert _get_model_id(settings) == "meta-llama/llama-3.3-70b"
