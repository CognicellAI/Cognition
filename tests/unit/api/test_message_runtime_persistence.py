"""Regression tests for durable session progress during runtime activity."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from server.app.agent.runtime import (
    ContextEvent,
    DoneEvent,
    ErrorEvent,
    SandboxLifecycleEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from server.app.api.models import MessageCreate
from server.app.api.routes.messages import agent_event_stream, send_message
from server.app.api.scoping import SessionScope
from server.app.llm.deep_agent_service import DeepAgentStreamingService
from server.app.models import RunStatus, SessionConfig, SessionStatus
from server.app.rate_limiter import RateLimitConfig, RateLimiter
from server.app.runtime_projection import RuntimeProjectionService
from server.app.settings import Settings
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore
from server.app.storage.memory import MemoryStorageBackend


class _FakeRequest(SimpleNamespace):
    async def is_disconnected(self) -> bool:
        return False


class _FakeService:
    def __init__(self, events: list[Any], checkpoint_messages: list[Any] | None = None) -> None:
        self._events = events
        self._checkpoint_messages = checkpoint_messages
        self.rebuild_calls: list[tuple[str, str]] = []
        self.store: MemoryStorageBackend | None = None

    async def stream_response(self, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        for event in self._events:
            yield event

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        scope: dict[str, str] | None = None,
    ) -> int:
        self.rebuild_calls.append((session_id, thread_id))
        if self.store is None or self._checkpoint_messages is None:
            return 0
        return await self.store.rebuild_message_projection(
            session_id=session_id,
            thread_id=thread_id,
            checkpoint_messages=self._checkpoint_messages,
            effective_scope=scope,
        )


class _FakeAgentManager:
    def __init__(
        self,
        service: _FakeService,
        active_runtime_count: int = 1,
        sandbox_snapshot: Any | None = None,
    ) -> None:
        self._service = service
        self._active_runtime_count = active_runtime_count
        self._sandbox_snapshot = sandbox_snapshot
        self.release_calls: list[str] = []

    def get_service(self, _session_id: str) -> _FakeService:
        return self._service

    def register_session(self, _session_id: str, _workspace_path: str) -> _FakeService:
        return self._service

    def drain_sandbox_events(self, _session_id: str) -> list[Any]:
        return []

    def snapshot_sandbox_backend(
        self,
        _session_id: str,
        phase: str = "runtime_snapshot",
    ) -> Any | None:
        if self._sandbox_snapshot is not None:
            self._sandbox_snapshot.phase = phase
        return self._sandbox_snapshot

    def snapshot_sandbox_backend_events(
        self,
        _session_id: str,
        *,
        include_runtime_snapshot: bool = True,
    ) -> list[Any]:
        if not include_runtime_snapshot:
            return []
        if self._sandbox_snapshot is None:
            return []
        self._sandbox_snapshot.phase = "runtime_snapshot"
        return [self._sandbox_snapshot]

    def active_runtime_count(self, _session_id: str) -> int:
        return self._active_runtime_count

    def release_sandbox_backend(self, session_id: str) -> None:
        self.release_calls.append(session_id)


def _settings() -> Settings:
    settings = MagicMock(spec=Settings)
    settings.scoping_enabled = False
    settings.sse_heartbeat_interval_seconds = 30
    settings.callback_allowed_origins = ["https://example.com"]
    return cast(Settings, settings)


async def _config_store(tmp_path: Any) -> DefaultConfigStore:
    store = DefaultConfigStore(
        MemoryConfigRegistry(),
        workspace_path=tmp_path,
    )
    await store.upsert_agent(
        "test-agent",
        {},
        {
            "name": "test-agent",
            "system_prompt": "Test agent.",
            "mode": "primary",
        },
        "api",
    )
    return store


@pytest.mark.asyncio
async def test_agent_stream_done_includes_assistant_data(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-runtime-assistant",
        thread_id="thread-runtime-assistant",
        config=SessionConfig(),
        agent_name="test-agent",
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
    assert manager.release_calls == []

    updated = await store.get_session(session.id)
    assert updated is not None
    assert updated.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_tool_activity_updates_session_and_message_projection(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-runtime-tools",
        thread_id="thread-runtime-tools",
        config=SessionConfig(),
        agent_name="test-agent",
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
async def test_done_keeps_session_active_when_other_runtime_active(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-overlap",
        thread_id="thread-overlap",
        config=SessionConfig(),
        agent_name="test-agent",
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
    assert manager.release_calls == []


@pytest.mark.asyncio
async def test_terminal_run_rebuilds_message_projection_from_checkpoint(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-checkpoint-projection",
        thread_id="thread-checkpoint-projection",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    run = await store.create_run(
        run_id="run-checkpoint-projection",
        session_id=session.id,
        thread_id=session.thread_id,
        status=RunStatus.ACTIVE,
    )
    service = _FakeService(
        [
            ToolCallEvent(name="execute", args={"cmd": "pytest"}, tool_call_id="call-1"),
            ToolResultEvent(tool_call_id="call-1", output="transient", exit_code=0),
            DoneEvent(),
        ],
        checkpoint_messages=[
            HumanMessage(content="run tests"),
            AIMessage(content="checkpoint answer"),
            ToolMessage(content="checkpoint tool result", tool_call_id="call-1"),
        ],
    )
    service.store = store
    manager = _FakeAgentManager(service)
    projection = RuntimeProjectionService(store)

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
            run=run,
            projection=projection,
        )
    ]

    messages = await store.list_messages_for_session(session.id)
    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[1].content == "checkpoint answer"
    assert messages[2].content == "checkpoint tool result"
    assert service.rebuild_calls == [(session.id, session.thread_id)]

    events = await store.list_events(session.id, run_id=run.id)
    assert "message.projection.rebuilt" in [event.event_type for event in events]


@pytest.mark.asyncio
async def test_run_state_sse_uses_durable_transition_correlation(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-run-state-correlation",
        thread_id="thread-run-state-correlation",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    run = await store.create_run(
        run_id="run-state-correlation",
        session_id=session.id,
        thread_id=session.thread_id,
        status=RunStatus.ACTIVE,
    )
    service = _FakeService([DoneEvent()])
    manager = _FakeAgentManager(service, active_runtime_count=0)
    projection = RuntimeProjectionService(store)

    events = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "finish",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
            run=run,
            projection=projection,
        )
    ]

    run_state = next(event for event in events if event["event"] == "run_state")
    assert run_state["data"]["run_id"] == run.id
    assert run_state["data"]["session_id"] == session.id
    assert run_state["data"]["event_type"] == "run.done"
    assert run_state["data"]["to_status"] == "idle"
    assert run_state["data"]["sequence"] >= 1

    updated = await store.get_session(session.id)
    assert updated is not None
    assert updated.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_context_sse_overwrites_upstream_ids_with_durable_correlation(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-context-correlation",
        thread_id="thread-context-correlation",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    run = await store.create_run(
        run_id="run-context-correlation",
        session_id=session.id,
        thread_id=session.thread_id,
        status=RunStatus.ACTIVE,
    )
    manager = _FakeAgentManager(
        _FakeService(
            [
                ContextEvent(
                    action="policy_resolved",
                    session_id="upstream-session",
                    run_id="upstream-run",
                    scope_keys=["upstream"],
                ),
                DoneEvent(),
            ]
        ),
        active_runtime_count=0,
    )
    projection = RuntimeProjectionService(store)

    events = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "context",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
            run=run,
            projection=projection,
        )
    ]

    context = next(event for event in events if event["event"] == "context")
    assert context["data"]["session_id"] == session.id
    assert context["data"]["run_id"] == run.id
    assert context["data"]["event_type"] == "context.policy_resolved"
    assert context["data"]["sequence"] >= 1


@pytest.mark.asyncio
async def test_sandbox_lifecycle_metadata_is_streamed_and_persisted(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-sandbox-lifecycle",
        thread_id="thread-sandbox-lifecycle",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    run = await store.create_run(
        run_id="run-sandbox-lifecycle",
        session_id=session.id,
        thread_id=session.thread_id,
        status=RunStatus.ACTIVE,
    )
    service = _FakeService([DoneEvent()])
    sandbox_snapshot = SandboxLifecycleEvent(
        sandbox_id="microvm-123",
        phase="runtime_snapshot",
        sandbox_backend="aws_lambda_microvm",
        metadata={
            "microvm_id": "microvm-123",
            "endpoint": "https://example.aws",
            "image": "arn:aws:lambda:us-west-2:123:microvm-image/runtime",
            "status": "RUNNING",
            "execution_role_fingerprint": "abcd1234",
            "proxy_auth": "do-not-stream",
            "nested": {"auth_token": "do-not-persist", "port": 8080},
        },
    )
    manager = _FakeAgentManager(
        service,
        active_runtime_count=0,
        sandbox_snapshot=sandbox_snapshot,
    )
    projection = RuntimeProjectionService(store)

    events = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "sandbox status",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
            run=run,
            projection=projection,
        )
    ]

    lifecycle = next(event for event in events if event["event"] == "sandbox_lifecycle")
    streamed_metadata = lifecycle["data"]["metadata"]
    assert streamed_metadata["microvm_id"] == "microvm-123"
    assert streamed_metadata["endpoint"] == "https://example.aws"
    assert streamed_metadata["nested"] == {"port": 8080}
    assert "proxy_auth" not in streamed_metadata

    durable_events = await store.list_events(session.id, run_id=run.id)
    sandbox_event = next(
        event for event in durable_events if event.event_type == "sandbox.runtime_snapshot"
    )
    persisted_metadata = sandbox_event.payload["metadata"]
    assert persisted_metadata["microvm_id"] == "microvm-123"
    assert persisted_metadata["execution_role_fingerprint"] == "abcd1234"
    assert "do-not" not in str(persisted_metadata)


@pytest.mark.asyncio
async def test_error_event_terminates_run_without_done(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-error-terminal",
        thread_id="thread-error-terminal",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    run = await store.create_run(
        run_id="run-error-terminal",
        session_id=session.id,
        thread_id=session.thread_id,
        status=RunStatus.ACTIVE,
    )
    service = _FakeService(
        [
            ErrorEvent(
                message="This behavior is only available via the new `ls` API.",
                code="RUNTIME_ERROR",
            ),
            DoneEvent(),
        ]
    )
    manager = _FakeAgentManager(service, active_runtime_count=0)
    projection = RuntimeProjectionService(store)

    events = [
        event
        async for event in agent_event_stream(
            session.id,
            session.thread_id,
            "list files",
            session.workspace_path,
            _settings(),
            manager,  # type: ignore[arg-type]
            store,
            run=run,
            projection=projection,
        )
    ]

    assert [event["event"] for event in events if event["event"] == "done"] == []
    assert any(event["event"] == "error" for event in events)

    updated_run = await store.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == RunStatus.FAILED
    assert updated_run.error_code == "RUNTIME_ERROR"

    updated_session = await store.get_session(session.id)
    assert updated_session is not None
    assert updated_session.status == SessionStatus.FAILED
    assert manager.release_calls == [session.id]

    durable_event_types = [
        event.event_type for event in await store.list_events(session.id, run_id=run.id)
    ]
    assert "run.failed" in durable_event_types
    assert "run.error" in durable_event_types
    assert "run.done" not in durable_event_types


@pytest.mark.asyncio
async def test_empty_checkpoint_does_not_wipe_existing_message_projection() -> None:
    checkpointer = MagicMock()
    checkpointer.aget = AsyncMock(return_value={"channel_values": {"messages": []}})
    storage = MagicMock()
    storage.get_checkpointer = AsyncMock(return_value=checkpointer)
    storage.list_messages_for_session = AsyncMock(return_value=[MagicMock(), MagicMock()])
    storage.rebuild_message_projection = AsyncMock()

    service = DeepAgentStreamingService(Settings())
    service.storage_backend = storage

    rebuilt = await service.rebuild_message_projection(
        session_id="session-empty-checkpoint",
        thread_id="thread-empty-checkpoint",
    )

    assert rebuilt == 2
    storage.rebuild_message_projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_events_capture_current_trace_context(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-trace-context",
        thread_id="thread-trace-context",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    projection = RuntimeProjectionService(store)

    span_events: list[tuple[str, dict[str, Any] | None]] = []

    def _fake_span_event(name: str, attributes: dict[str, Any] | None = None) -> None:
        span_events.append((name, attributes))

    with (
        patch("server.app.runtime_projection.add_span_event", new=_fake_span_event),
        patch(
            "server.app.runtime_projection.current_trace_context",
            return_value=("trace-123", "span-456"),
        ),
    ):
        run = await projection.begin_run(session=session)
        event = await projection.append_event(run, "tool.call.started")

    assert run.trace_id == "trace-123"
    assert event.trace_id == "trace-123"
    assert event.span_id == "span-456"

    with (
        patch("server.app.runtime_projection.add_span_event", new=_fake_span_event),
        patch(
            "server.app.runtime_projection.current_trace_context",
            return_value=("trace-drift", "span-drift"),
        ),
    ):
        drifted_event = await projection.append_event(run, "tool.call.completed")

    assert drifted_event.trace_id == "trace-123"
    assert drifted_event.span_id is None
    assert [name for name, _attributes in span_events] == [
        "cognition.run.begin",
        "cognition.runtime_event",
        "cognition.runtime_event",
        "cognition.runtime_event",
    ]


@pytest.mark.asyncio
async def test_send_message_rejects_concurrent_runtime_turn(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-active-turn",
        thread_id="thread-active-turn",
        config=SessionConfig(),
        agent_name="test-agent",
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
            artifact_store=cast(Any, None),
            config_store=await _config_store(tmp_path),
            rate_limiter=rate_limiter,
            scope=SessionScope({}),
        )

    assert exc.value.status_code == 409
    messages = await store.list_messages_for_session(session.id)
    assert messages == []


@pytest.mark.asyncio
async def test_completion_callback_emits_durable_delivery_events(tmp_path) -> None:
    store = MemoryStorageBackend(workspace_path=str(tmp_path))
    session = await store.create_session(
        session_id="session-callback-events",
        thread_id="thread-callback-events",
        config=SessionConfig(),
        agent_name="test-agent",
    )
    manager = _FakeAgentManager(
        _FakeService([TokenEvent(content="done"), DoneEvent()]),
        active_runtime_count=0,
    )
    rate_limiter = RateLimiter(RateLimitConfig(requests_per_minute=1000, burst_size=1000))

    async def _fake_callback(*args: Any, **kwargs: Any) -> None:
        projection = kwargs["projection"]
        run = kwargs["run"]
        await projection.append_event(run, "callback.delivery.started", payload={})
        await projection.append_event(run, "callback.delivery.completed", payload={})

    with patch(
        "server.app.api.routes.messages._post_completion_callback",
        new=AsyncMock(side_effect=_fake_callback),
    ):
        response = await send_message(
            session.id,
            MessageCreate(
                content="status?",
                parent_id=None,
                model=None,
                callback_url=cast(Any, "https://example.com/callback"),
            ),
            http_request=cast(
                Any,
                _FakeRequest(client=SimpleNamespace(host="test"), headers={}),
            ),
            settings=_settings(),
            agent_manager=manager,  # type: ignore[arg-type]
            store=store,
            artifact_store=cast(Any, None),
            config_store=await _config_store(tmp_path),
            rate_limiter=rate_limiter,
            scope=SessionScope({}),
        )

        async for _chunk in response.body_iterator:
            pass

    runs = await store.list_runs(session.id)
    assert len(runs) == 1
    events = await store.list_events(session.id, run_id=runs[0].id)
    event_types = [event.event_type for event in events]
    assert "callback.delivery.started" in event_types
    assert "callback.delivery.completed" in event_types
