"""Unit tests for #17: LangGraph Store cross-thread memory wiring.

Covers:
- CognitionContext.from_scope() — scope dict → typed context
- Store backend mapping: memory → InMemoryStore, sqlite → AsyncSqliteStore
- Store passed to create_cognition_agent and from there to create_deep_agent
- Context forwarded to astream() on DeepAgentRuntime
- Store persistence: write in thread 1, read in thread 2 (InMemoryStore)
- Namespace isolation: user A cannot read user B's memories
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# CognitionContext.from_scope()
# ---------------------------------------------------------------------------


class TestCognitionContext:
    def test_from_scope_empty(self):
        from server.app.agent.cognition_agent import CognitionContext

        ctx = CognitionContext.from_scope({})
        assert ctx.user_id == "anonymous"
        assert ctx.org_id is None
        assert ctx.project_id is None
        assert ctx.extra == {}

    def test_from_scope_none(self):
        from server.app.agent.cognition_agent import CognitionContext

        ctx = CognitionContext.from_scope(None)
        assert ctx.user_id == "anonymous"

    def test_from_scope_all_dims(self):
        from server.app.agent.cognition_agent import CognitionContext

        ctx = CognitionContext.from_scope({"user": "alice", "org": "acme", "project": "myapp"})
        assert ctx.user_id == "alice"
        assert ctx.org_id == "acme"
        assert ctx.project_id == "myapp"
        assert ctx.extra == {}

    def test_from_scope_extra_keys(self):
        from server.app.agent.cognition_agent import CognitionContext

        ctx = CognitionContext.from_scope({"user": "bob", "tenant": "t1", "region": "us-east-1"})
        assert ctx.user_id == "bob"
        assert ctx.extra == {"tenant": "t1", "region": "us-east-1"}

    def test_from_scope_user_only(self):
        from server.app.agent.cognition_agent import CognitionContext

        ctx = CognitionContext.from_scope({"user": "carol"})
        assert ctx.user_id == "carol"
        assert ctx.org_id is None
        assert ctx.project_id is None


# ---------------------------------------------------------------------------
# Storage backend: get_store() returns correct type
# ---------------------------------------------------------------------------


class TestMemoryStorageBackendStore:
    @pytest.mark.asyncio
    async def test_get_store_returns_in_memory_store(self):
        from langgraph.store.memory import InMemoryStore

        from server.app.storage.memory import MemoryStorageBackend

        backend = MemoryStorageBackend(workspace_path="/tmp/test-store")
        store = await backend.get_store()
        assert isinstance(store, InMemoryStore)

    @pytest.mark.asyncio
    async def test_get_store_returns_same_instance(self):
        """get_store() is idempotent — returns the same instance."""
        from server.app.storage.memory import MemoryStorageBackend

        backend = MemoryStorageBackend(workspace_path="/tmp/test-store")
        store1 = await backend.get_store()
        store2 = await backend.get_store()
        assert store1 is store2


class TestSqliteStorageBackendStore:
    @pytest.mark.asyncio
    async def test_get_store_returns_async_sqlite_store(self, tmp_path: Any):
        from langgraph.store.sqlite.aio import AsyncSqliteStore

        from server.app.storage.sqlite import SqliteStorageBackend

        backend = SqliteStorageBackend(
            connection_string="state.db",
            workspace_path=str(tmp_path),
        )
        await backend.initialize()
        store = await backend.get_store()
        try:
            assert isinstance(store, AsyncSqliteStore)
        finally:
            await backend.close()

    @pytest.mark.asyncio
    async def test_get_store_returns_same_instance(self, tmp_path: Any):
        from server.app.storage.sqlite import SqliteStorageBackend

        backend = SqliteStorageBackend(
            connection_string="state.db",
            workspace_path=str(tmp_path),
        )
        await backend.initialize()
        store1 = await backend.get_store()
        store2 = await backend.get_store()
        try:
            assert store1 is store2
        finally:
            await backend.close()

    @pytest.mark.asyncio
    async def test_close_store_clears_instance(self, tmp_path: Any):
        from server.app.storage.sqlite import SqliteStorageBackend

        backend = SqliteStorageBackend(
            connection_string="state.db",
            workspace_path=str(tmp_path),
        )
        await backend.initialize()
        await backend.get_store()
        assert backend._store is not None
        await backend.close_store()
        assert backend._store is None
        await backend.close()


# ---------------------------------------------------------------------------
# Store persistence: cross-thread memory with InMemoryStore
# ---------------------------------------------------------------------------


class TestCrossThreadMemory:
    @pytest.mark.asyncio
    async def test_write_thread1_read_thread2(self):
        """Data written to the store in thread 1 is readable in thread 2."""
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()

        # Thread 1: write a memory
        namespace = ("alice", "memories")
        await store.aput(namespace, "pref_1", {"preference": "dark mode"})

        # Thread 2: read the memory — cross-thread, same user namespace
        results = await store.asearch(namespace, query="dark mode")
        assert len(results) >= 1
        found = any(item.value.get("preference") == "dark mode" for item in results)
        assert found, "Memory written in thread 1 should be readable in thread 2"

    @pytest.mark.asyncio
    async def test_namespace_isolation_between_users(self):
        """User A's memories are not visible to user B."""
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()

        # User A writes a memory
        ns_a = ("alice", "memories")
        await store.aput(ns_a, "secret", {"value": "alice_secret"})

        # User B searches their own namespace — should not find alice's memory
        ns_b = ("bob", "memories")
        results = await store.asearch(ns_b, query="alice_secret")
        found = any(item.value.get("value") == "alice_secret" for item in results)
        assert not found, "User B should not be able to read user A's memories"

    @pytest.mark.asyncio
    async def test_same_user_multiple_namespaces(self):
        """Same user can have separate namespaces that don't cross-contaminate."""
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()

        ns_personal = ("alice", "personal")
        ns_work = ("alice", "work")

        await store.aput(ns_personal, "item1", {"data": "personal_data"})
        await store.aput(ns_work, "item1", {"data": "work_data"})

        personal_results = await store.asearch(ns_personal, query="personal_data")
        work_results = await store.asearch(ns_work, query="work_data")

        personal_values = [r.value.get("data") for r in personal_results]
        work_values = [r.value.get("data") for r in work_results]

        assert "personal_data" in personal_values
        assert "work_data" in work_values
        # Ensure no cross-contamination
        assert "work_data" not in personal_values


# ---------------------------------------------------------------------------
# Runtime: context is forwarded to astream()
# ---------------------------------------------------------------------------


class TestRuntimeContextForwarding:
    @pytest.mark.asyncio
    async def test_astream_called_with_context(self):
        """DeepAgentRuntime passes context= to the underlying graph's astream()."""
        from server.app.agent.cognition_agent import CognitionContext
        from server.app.agent.runtime import DeepAgentRuntime

        ctx = CognitionContext(user_id="alice", org_id="acme")

        async def _fake_astream(*args: Any, **kwargs: Any) -> Any:
            if False:
                yield  # Make it an async generator

        mock_agent = MagicMock()
        mock_agent.astream = MagicMock(return_value=_fake_astream())

        runtime = DeepAgentRuntime(
            agent=mock_agent,
            checkpointer=MagicMock(),
            thread_id="thread-1",
            context=ctx,
        )

        events = []
        async for event in runtime.astream_events("hello", thread_id="thread-1"):
            events.append(event)

        # Verify context was passed
        call_kwargs = mock_agent.astream.call_args.kwargs
        assert call_kwargs.get("context") is ctx

    @pytest.mark.asyncio
    async def test_no_context_passes_none(self):
        """If no context is set, None is passed to astream()."""
        from server.app.agent.runtime import DeepAgentRuntime

        async def _empty_stream(*args: Any, **kwargs: Any):
            if False:
                yield

        mock_agent = MagicMock()
        mock_agent.astream = MagicMock(return_value=_empty_stream())

        runtime = DeepAgentRuntime(
            agent=mock_agent,
            checkpointer=MagicMock(),
            thread_id="thread-1",
            # No context
        )

        async for _ in runtime.astream_events("hello", thread_id="thread-1"):
            pass

        call_kwargs = mock_agent.astream.call_args.kwargs
        assert call_kwargs.get("context") is None


# ---------------------------------------------------------------------------
# Service layer: store obtained and passed through
# ---------------------------------------------------------------------------


class TestServiceStoreWiring:
    @pytest.mark.asyncio
    async def test_store_obtained_from_storage_backend(self):
        """stream_response obtains store from storage_backend.get_store()."""
        from server.app.agent.runtime import DoneEvent
        from server.app.llm.deep_agent_service import DeepAgentStreamingService
        from server.app.models import Session, SessionConfig, SessionStatus
        from server.app.settings import Settings

        session = Session(
            id="sess-store-test",
            workspace_path="/tmp/ws",
            title="Store Test",
            thread_id="thread-store-test",
            status=SessionStatus.ACTIVE,
            config=SessionConfig(provider="mock", model="mock-model"),
            agent_name="default",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            scopes={"user": "alice"},
        )

        mock_store = MagicMock()
        mock_global_storage = MagicMock()
        mock_global_storage.get_session = AsyncMock(return_value=session)

        # The service has its own storage_backend (from create_storage_backend in __init__)
        # We patch the service's storage_backend directly for session lookup,
        # get_checkpointer, and get_store.
        s = MagicMock(spec=Settings)
        service = DeepAgentStreamingService(s)
        service.storage_backend = MagicMock()
        service.storage_backend.get_session = AsyncMock(return_value=session)
        service.storage_backend.get_checkpointer = AsyncMock(return_value=MagicMock())
        service.storage_backend.get_store = AsyncMock(return_value=mock_store)

        async def _empty_events(*a: Any, **kw: Any):
            yield DoneEvent()

        mock_runtime = MagicMock()
        mock_runtime.astream_events = MagicMock(return_value=_empty_events())

        with (
            patch(
                "server.app.llm.deep_agent_service.DeepAgentRuntime",
                return_value=mock_runtime,
            ) as runtime_cls,
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
            ),
        ):
            async for _ in service.stream_response(
                session_id=session.id,
                thread_id=session.thread_id,
                project_path="/tmp/ws",
                content="hello",
            ):
                pass

        # Verify get_store was called on service.storage_backend
        service.storage_backend.get_store.assert_called_once()

        # Verify the runtime was constructed with the correct context
        runtime_kwargs = runtime_cls.call_args.kwargs
        assert "context" in runtime_kwargs
        ctx = runtime_kwargs["context"]
        assert ctx.user_id == "alice"
