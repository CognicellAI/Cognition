"""Regression tests for durable session progress during runtime activity."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from server.app.agent.runtime import DoneEvent, TokenEvent, ToolCallEvent, ToolResultEvent
from server.app.api.models import MessageCreate
from server.app.api.routes.messages import agent_event_stream, send_message
from server.app.api.scoping import SessionScope
from server.app.models import SessionConfig, SessionStatus
from server.app.rate_limiter import RateLimitConfig, RateLimiter
from server.app.settings import Settings
from server.app.storage.memory import MemoryStorageBackend


class _FakeService:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def stream_response(self, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        for event in self._events:
            yield event


class _FakeAgentManager:
    def __init__(self, service: _FakeService, active_runtime_count: int = 1) -> None:
        self._service = service
        self._active_runtime_count = active_runtime_count

    def get_service(self, _session_id: str) -> _FakeService:
        return self._service

    def register_session(self, _session_id: str, _workspace_path: str) -> _FakeService:
        return self._service

    def drain_sandbox_events(self, _session_id: str) -> list[Any]:
        return []

    def active_runtime_count(self, _session_id: str) -> int:
        return self._active_runtime_count


def _settings() -> Settings:
    settings = MagicMock(spec=Settings)
    settings.scoping_enabled = False
    return cast(Settings, settings)


@pytest.mark.asyncio
async def test_agent_stream_done_includes_assistant_data(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-runtime-assistant",
        thread_id="thread-runtime-assistant",
        config=SessionConfig(),
    )
    manager = _FakeAgentManager(
        _FakeService(
            [
                TokenEvent(content="Actively working."),
                DoneEvent(),
            ]
        )
    )

    events = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "status?",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
        )
    ]

    done = next(event for event in events if event["event"] == "done")
    assert done["data"]["assistant_data"]["content"] == "Actively working."

    updated = await store.get_session(session.id)
    assert updated is not None
    assert updated.status == SessionStatus.DONE


@pytest.mark.asyncio
async def test_tool_activity_updates_session_and_message_projection(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-runtime-tools",
        thread_id="thread-runtime-tools",
        config=SessionConfig(),
    )
    original_updated_at = session.updated_at
    manager = _FakeAgentManager(
        _FakeService(
            [
                ToolCallEvent(name="execute", args={"cmd": "pytest"}, tool_call_id="call-1"),
                ToolResultEvent(tool_call_id="call-1", output="passed", exit_code=0),
                DoneEvent(),
            ]
        )
    )

    _ = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "run tests",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
        )
    ]

    messages = await store.list_messages_for_session(session.id)
    assert [message.role for message in messages] == ["assistant", "tool"]
    assert messages[0].tool_calls is not None
    assert messages[0].tool_calls[0].name == "execute"
    assert messages[1].tool_call_id == "call-1"

    updated = await store.get_session(session.id)
    assert updated is not None
    assert updated.message_count == 2
    assert updated.updated_at > original_updated_at


@pytest.mark.asyncio
async def test_done_does_not_mark_session_done_when_other_runtime_active(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-overlap",
        thread_id="thread-overlap",
        config=SessionConfig(),
    )
    manager = _FakeAgentManager(_FakeService([DoneEvent()]), active_runtime_count=2)

    _ = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "ping",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
        )
    ]

    updated = await store.get_session(session.id)
    assert updated is not None
    assert updated.status == SessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_send_message_rejects_concurrent_runtime_turn(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-active-turn",
        thread_id="thread-active-turn",
        config=SessionConfig(),
    )
    manager = _FakeAgentManager(_FakeService([]), active_runtime_count=1)
    rate_limiter = RateLimiter(RateLimitConfig(requests_per_minute=1000, burst_size=1000))

    with pytest.raises(HTTPException) as exc:
        await send_message(
            session.id,
            MessageCreate(content="status?", parent_id=None, model=None),
            http_request=cast(Any, SimpleNamespace(client=SimpleNamespace(host="test"))),
            settings=_settings(),
            agent_manager=manager,  # type: ignore[arg-type]
            store=store,
            rate_limiter=rate_limiter,
            scope=SessionScope({}),
        )

    assert exc.value.status_code == 409
    messages = await store.list_messages_for_session(session.id)
    assert messages == []
