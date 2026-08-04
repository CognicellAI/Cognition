"""Tests for REST API.

Tests for the Phase 5 REST API implementation with workspace-based sessions.
"""

import asyncio
import tempfile
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import (
    get_artifact_store,
    get_settings_dep,
    get_storage_backend_dep,
    set_config_store,
)
from server.app.main import app
from server.app.models import RunStatus, SessionStatus
from server.app.runtime_projection import RuntimeProjectionService
from server.app.settings import Settings

# Create test client
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_agent_registry():
    """Initialize agent registry for tests."""
    import tempfile

    from server.app.api.dependencies import set_model_catalog_dep
    from server.app.llm.model_catalog import ModelCatalog
    from server.app.settings import get_settings
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    with tempfile.TemporaryDirectory() as tmpdir:
        set_config_store(DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmpdir))
        s = get_settings()
        set_model_catalog_dep(ModelCatalog(catalog_url=s.model_catalog_url))
        yield


class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_health_check(self):
        """Test health endpoint returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "active_sessions" in data
        assert "timestamp" in data

    def test_health_active_sessions_excludes_terminal_sessions(self):
        """Health active_sessions reports non-terminal sessions only."""

        class FakeStorage:
            async def list_sessions(self):
                from types import SimpleNamespace

                return [
                    SimpleNamespace(status=SessionStatus.IDLE),
                    SimpleNamespace(status=SessionStatus.ACTIVE),
                    SimpleNamespace(status=SessionStatus.WAITING_FOR_APPROVAL),
                    SimpleNamespace(status=SessionStatus.DONE),
                    SimpleNamespace(status=SessionStatus.FAILED),
                    SimpleNamespace(status=SessionStatus.ABORTED),
                    SimpleNamespace(status=SessionStatus.EXPIRED),
                ]

        app.dependency_overrides[get_storage_backend_dep] = lambda: FakeStorage()
        try:
            response = client.get("/health")
        finally:
            app.dependency_overrides.pop(get_storage_backend_dep, None)

        assert response.status_code == 200
        assert response.json()["active_sessions"] == 3

    def test_ready_check(self):
        """Test ready endpoint returns ready status."""
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True

    def test_ready_check_fails_closed_when_selected_artifact_store_is_unavailable(self):
        """Readiness must not hide failure of the builder-selected durable store."""

        class UnavailableArtifactStore:
            async def health_check(self) -> None:
                raise ConnectionError("private endpoint details")

        app.dependency_overrides[get_artifact_store] = lambda: UnavailableArtifactStore()
        try:
            response = client.get("/ready")
        finally:
            app.dependency_overrides.pop(get_artifact_store, None)

        assert response.status_code == 503
        assert response.json() == {"ready": False}
        assert "private endpoint details" not in response.text

    def test_general_exception_response_redacts_internal_error(self):
        """Unhandled 500 responses must not expose raw exception text."""

        class FailingStorage:
            async def list_sessions(self):
                raise RuntimeError("secret database path /private/tenant/acme")

        app.dependency_overrides[get_storage_backend_dep] = lambda: FailingStorage()
        redacting_client = TestClient(app, raise_server_exceptions=False)
        try:
            response = redacting_client.get("/health")
        finally:
            app.dependency_overrides.pop(get_storage_backend_dep, None)

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert "secret database path" not in response.text


class TestSessionEndpoints:
    """Test session API endpoints."""

    def test_create_session(self):
        """Test creating a session."""
        response = client.post(
            "/sessions",
            json={
                "agent_name": "default",
                "title": "Test Session",
                "metadata": {"workflow": "review", "repo": "acme/app"},
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Session"
        assert data["metadata"] == {"workflow": "review", "repo": "acme/app"}
        assert "id" in data
        assert "thread_id" in data
        # Note: No workspace_path or config in response (server uses global settings)

    def test_create_session_validation(self):
        """Test session creation validation."""
        # Title too long should fail
        response = client.post("/sessions", json={"agent_name": "default", "title": "x" * 201})
        assert response.status_code == 422

    def test_list_sessions(self):
        """Test listing sessions."""
        # Create a session first
        client.post(
            "/sessions",
            json={
                "agent_name": "default",
                "title": "list-test-session",
                "metadata": {"repo": "myorg/myrepo"},
            },
        )

        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

        filtered = client.get("/sessions?metadata.repo=myorg/myrepo")
        assert filtered.status_code == 200
        filtered_data = filtered.json()
        assert any(
            session["metadata"].get("repo") == "myorg/myrepo"
            for session in filtered_data["sessions"]
        )

    def test_get_session(self):
        """Test getting a session."""
        # Create a session
        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "get-test-session"}
        )
        session_id = create_resp.json()["id"]

        # Get the session
        response = client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert data["title"] == "get-test-session"

    def test_get_session_not_found(self):
        """Test getting a non-existent session."""
        response = client.get("/sessions/non-existent-id")
        assert response.status_code == 404

    def test_get_session_context_debug_metadata(self):
        """Context debug endpoint returns redacted policy/token metadata."""
        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "context-debug"}
        )
        session_id = create_resp.json()["id"]

        response = client.get(f"/sessions/{session_id}/context")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["agent_name"] == "default"
        assert data["scope_keys"] == []
        assert data["policy"] == {}
        assert data["message_count"] == 0
        assert data["estimated_tokens"] is None
        assert data["messages"] == []

    def test_get_session_context_debug_redacts_message_content(self):
        """Context debug endpoint returns counts and IDs, not raw message content."""
        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "context-debug-redaction"}
        )
        session_id = create_resp.json()["id"]
        message_id = str(uuid.uuid4())

        async def _create_message() -> None:
            store = get_storage_backend_dep()
            await store.create_message(
                message_id=message_id,
                session_id=session_id,
                role="user",
                content="secret customer content should not appear",
                token_count=None,
            )

        asyncio.run(_create_message())

        response = client.get(f"/sessions/{session_id}/context")

        assert response.status_code == 200
        data = response.json()
        assert data["message_count"] == 1
        assert data["estimated_tokens"] is None
        assert data["messages"][0]["id"] == message_id
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["estimated_tokens"] is None
        assert "content" not in data["messages"][0]
        assert "secret customer content" not in response.text

    def test_update_session(self):
        """Test updating a session."""
        # Create a session
        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "original-title"}
        )
        session_id = create_resp.json()["id"]

        # Update the session
        response = client.patch(
            f"/sessions/{session_id}",
            json={
                "agent_name": "default",
                "title": "updated-title",
                "metadata": {"ticket": "ABC-123"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "updated-title"
        assert data["metadata"] == {"ticket": "ABC-123"}

    def test_update_session_model_only_resolves_unambiguous_provider(self):
        provider_resp = client.post(
            "/models/providers",
            json={
                "id": "session-openai-compatible",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )
        assert provider_resp.status_code == 201

        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "session-provider-resolution"}
        )
        session_id = create_resp.json()["id"]

        response = client.patch(
            f"/sessions/{session_id}",
            json={"config": {"model": "google/gemini-3-flash-preview"}},
        )
        assert response.status_code == 200

    def test_update_session_model_only_rejects_ambiguous_provider_resolution(self):
        client.post(
            "/models/providers",
            json={
                "id": "session-openai-compatible-ambiguous",
                "provider": "openai_compatible",
                "model": "shared-model",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )
        client.post(
            "/models/providers",
            json={
                "id": "session-openai-ambiguous",
                "provider": "openai",
                "model": "shared-model",
            },
        )

        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "session-provider-ambiguous"}
        )
        session_id = create_resp.json()["id"]

        response = client.patch(
            f"/sessions/{session_id}",
            json={"config": {"model": "shared-model"}},
        )
        assert response.status_code == 422
        assert "multiple provider types" in response.json()["detail"]

    def test_update_session_model_only_rejects_unknown_model(self):
        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "session-provider-unknown-model"}
        )
        session_id = create_resp.json()["id"]

        response = client.patch(
            f"/sessions/{session_id}",
            json={"config": {"model": "not-configured-anywhere"}},
        )
        assert response.status_code == 422
        assert "is not configured on any enabled provider" in response.json()["detail"]

    def test_delete_session(self):
        """Test deleting a session."""
        # Create a session
        create_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "delete-test-session"}
        )
        session_id = create_resp.json()["id"]

        # Delete the session
        response = client.delete(f"/sessions/{session_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 404


class TestMessageEndpoints:
    """Test message API endpoints."""

    def test_list_messages(self):
        """Test listing messages."""
        # Create a session first
        session_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "msg-list-test"}
        )
        session_id = session_resp.json()["id"]

        response = client.get(f"/sessions/{session_id}/messages")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert "total" in data
        assert "has_more" in data

    def test_list_messages_session_not_found(self):
        """Test listing messages for non-existent session."""
        response = client.get("/sessions/non-existent/messages")
        assert response.status_code == 404

    def test_send_message_sse(self):
        """Test sending a message returns SSE stream."""
        # Create session first
        session_resp = client.post("/sessions", json={"agent_name": "default", "title": "sse-test"})
        session_id = session_resp.json()["id"]

        response = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Hello, world!"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        session_data = client.get(f"/sessions/{session_id}").json()
        assert session_data["latest_run_id"] is not None
        assert session_data["last_activity_at"] is not None

        runs_resp = client.get(f"/sessions/{session_id}/runs")
        assert runs_resp.status_code == 200
        runs_data = runs_resp.json()
        assert runs_data["total"] == 1
        assert runs_data["runs"][0]["session_id"] == session_id

        events_resp = client.get(f"/sessions/{session_id}/events")
        assert events_resp.status_code == 200
        event_types = [event["event_type"] for event in events_resp.json()["events"]]
        assert "run.started" in event_types
        assert "message.user.accepted" in event_types

    def test_send_message_accepts_callback_url(self):
        """Test sending a message with callback_url returns SSE stream."""
        session_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "callback-test"}
        )
        session_id = session_resp.json()["id"]

        with patch(
            "server.app.api.routes.messages._post_completion_callback",
            new=AsyncMock(),
        ) as mock_callback:
            response = client.post(
                f"/sessions/{session_id}/messages",
                json={
                    "content": "Hello, world!",
                    "callback_url": "https://example.com/callback",
                },
                headers={"Accept": "text/event-stream"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert mock_callback.await_count == 1


class TestRuntimeDurabilityEndpoints:
    """Test builder-facing run/event durability API contracts."""

    def test_run_and_event_endpoints_enforce_session_scope(self):
        scoped_settings = Settings(
            scoping_enabled=True,
            scope_keys=["tenant", "project"],
        )
        app.dependency_overrides[get_settings_dep] = lambda: scoped_settings
        headers = {
            "X-Cognition-Scope-Tenant": "acme",
            "X-Cognition-Scope-Project": "ios",
        }
        wrong_headers = {
            "X-Cognition-Scope-Tenant": "acme",
            "X-Cognition-Scope-Project": "web",
        }
        try:
            session_resp = client.post(
                "/sessions",
                json={"agent_name": "default", "title": "scoped-runtime-contract"},
                headers=headers,
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            async def _seed_run() -> str:
                store = get_storage_backend_dep()
                session = await store.get_session(
                    session_id,
                    {"tenant": "acme", "project": "ios"},
                )
                assert session is not None
                projection = RuntimeProjectionService(store)
                run = await projection.begin_run(session=session)
                await projection.append_event(
                    run,
                    "tool.call.started",
                    payload={"tool_name": "execute"},
                )
                return run.id

            run_id = asyncio.run(_seed_run())

            runs_resp = client.get(f"/sessions/{session_id}/runs", headers=headers)
            assert runs_resp.status_code == 200
            runs_data = runs_resp.json()
            assert runs_data["total"] == 1
            assert runs_data["runs"][0]["id"] == run_id
            assert runs_data["runs"][0]["scope_keys"] == ["project", "tenant"]

            run_resp = client.get(f"/sessions/{session_id}/runs/{run_id}", headers=headers)
            assert run_resp.status_code == 200
            assert run_resp.json()["id"] == run_id

            events_resp = client.get(f"/sessions/{session_id}/events", headers=headers)
            assert events_resp.status_code == 200
            event_types = [event["event_type"] for event in events_resp.json()["events"]]
            assert event_types == ["run.started", "tool.call.started"]

            assert (
                client.get(f"/sessions/{session_id}/runs", headers=wrong_headers).status_code == 404
            )
            assert (
                client.get(
                    f"/sessions/{session_id}/runs/{run_id}", headers=wrong_headers
                ).status_code
                == 404
            )
            assert (
                client.get(f"/sessions/{session_id}/events", headers=wrong_headers).status_code
                == 404
            )
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)

    def test_event_endpoint_filters_and_paginates_durable_activity(self):
        session_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "runtime-events-filtering"}
        )
        session_id = session_resp.json()["id"]

        async def _seed_events() -> tuple[str, str]:
            store = get_storage_backend_dep()
            session = await store.get_session(session_id)
            assert session is not None
            projection = RuntimeProjectionService(store)
            run = await projection.begin_run(session=session)
            await projection.append_event(
                run,
                "tool.call.started",
                payload={"tool_name": "execute"},
                visibility="internal",
            )
            await projection.append_event(
                run,
                "tool.call.completed",
                payload={"tool_name": "execute", "ok": True},
                visibility="builder",
            )
            return run.id, "tool.call.completed"

        run_id, expected_event_type = asyncio.run(_seed_events())

        by_run = client.get(f"/sessions/{session_id}/events", params={"run_id": run_id})
        assert by_run.status_code == 200
        assert by_run.json()["total"] == 3

        after_first = client.get(
            f"/sessions/{session_id}/events",
            params={"after_sequence": 1},
        )
        assert after_first.status_code == 200
        assert [event["event_type"] for event in after_first.json()["events"]] == [
            "tool.call.started",
            "tool.call.completed",
        ]

        builder_only = client.get(
            f"/sessions/{session_id}/events",
            params={"visibility": "builder"},
        )
        assert builder_only.status_code == 200
        assert [event["event_type"] for event in builder_only.json()["events"]] == [
            "run.started",
            "tool.call.completed",
        ]

        completed_only = client.get(
            f"/sessions/{session_id}/events",
            params={"event_type": expected_event_type},
        )
        assert completed_only.status_code == 200
        completed_data = completed_only.json()
        assert completed_data["total"] == 1
        assert completed_data["events"][0]["payload"] == {
            "tool_name": "execute",
            "ok": True,
        }

        first_page = client.get(f"/sessions/{session_id}/events", params={"limit": 1})
        assert first_page.status_code == 200
        first_page_data = first_page.json()
        assert first_page_data["total"] == 1
        assert first_page_data["has_more"] is True

    def test_terminal_run_clears_active_run_from_session_summary(self):
        session_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "runtime-summary"}
        )
        session_id = session_resp.json()["id"]

        async def _complete_run() -> str:
            store = get_storage_backend_dep()
            session = await store.get_session(session_id)
            assert session is not None
            projection = RuntimeProjectionService(store)
            run = await projection.begin_run(session=session)
            await projection.transition_run(run, RunStatus.DONE, reason="Complete")
            return run.id

        run_id = asyncio.run(_complete_run())

        session_data = client.get(f"/sessions/{session_id}").json()
        assert session_data["latest_run_id"] == run_id
        assert session_data["active_run_id"] is None
        assert session_data["latest_event_type"] == "run.done"
        assert session_data["last_activity_at"] is not None
        assert session_data["status"] == "idle"

    def test_message_idempotency_key_does_not_reuse_terminal_run_for_new_work(self):
        session_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "runtime-idempotency"}
        )
        session_id = session_resp.json()["id"]

        async def _seed_idempotent_run() -> str:
            store = get_storage_backend_dep()
            session = await store.get_session(session_id)
            assert session is not None
            projection = RuntimeProjectionService(store)
            run = await projection.begin_run(
                session=session,
                idempotency_key="assignment-1",
            )
            await projection.transition_run(run, RunStatus.DONE, reason="Complete")
            return run.id

        run_id = asyncio.run(_seed_idempotent_run())
        before = client.get(f"/sessions/{session_id}").json()
        assert before["latest_run_id"] == run_id
        assert before["message_count"] == 0

        duplicate = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Hello twice", "idempotency_key": "assignment-1"},
            headers={"Accept": "text/event-stream"},
        )

        assert duplicate.status_code == 409
        assert "already used" in duplicate.json()["detail"]

        after = client.get(f"/sessions/{session_id}").json()
        assert after["latest_run_id"] == before["latest_run_id"]
        assert after["message_count"] == before["message_count"]


class TestConfigEndpoints:
    """Test config API endpoints."""

    def test_get_config(self):
        """Test getting server config."""
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert "server" in data
        assert "llm" in data


class TestModelEndpoints:
    """Test model catalog API behavior."""

    @pytest.fixture(autouse=True)
    def reset_config_store(self):
        from server.app.api.dependencies import set_model_catalog_dep
        from server.app.llm.model_catalog import ModelCatalog
        from server.app.settings import get_settings
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        with tempfile.TemporaryDirectory() as tmpdir:
            set_config_store(DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmpdir))
            settings = get_settings()
            set_model_catalog_dep(ModelCatalog(catalog_url=settings.model_catalog_url))
            yield

    def test_list_models_returns_empty_without_configured_providers(self):
        response = client.get("/models")
        assert response.status_code == 200
        assert response.json()["models"] == []

    def test_list_models_returns_only_configured_provider_types(self):
        client.post(
            "/models/providers",
            json={"id": "catalog-openai", "provider": "openai", "model": "gpt-4o"},
        )

        response = client.get("/models")
        assert response.status_code == 200
        models = response.json()["models"]
        assert models
        assert all(model["provider"] == "openai" for model in models)

    def test_list_models_filters_to_requested_configured_provider(self):
        client.post(
            "/models/providers",
            json={"id": "catalog-openai-2", "provider": "openai", "model": "gpt-4o"},
        )
        client.post(
            "/models/providers",
            json={
                "id": "catalog-anthropic-2",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )

        response = client.get("/models", params={"provider": "anthropic"})
        assert response.status_code == 200
        models = response.json()["models"]
        assert models
        assert all(model["provider"] == "anthropic" for model in models)

    def test_list_models_excludes_unconfigured_provider_filter(self):
        client.post(
            "/models/providers",
            json={"id": "catalog-openai-3", "provider": "openai", "model": "gpt-4o"},
        )

        response = client.get("/models", params={"provider": "anthropic"})
        assert response.status_code == 200
        assert response.json()["models"] == []

    def test_list_models_openai_compatible_contributes_no_catalog_models(self):
        client.post(
            "/models/providers",
            json={
                "id": "catalog-openrouter",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )

        response = client.get("/models")
        assert response.status_code == 200
        assert response.json()["models"] == []


class TestAPIIntegration:
    """Integration tests for full workflows."""

    def test_full_workflow(self):
        """Test complete workflow."""
        # Create session
        session_resp = client.post(
            "/sessions", json={"agent_name": "default", "title": "integration-test"}
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # List sessions
        list_resp = client.get("/sessions")
        assert list_resp.status_code == 200
        assert any(s["id"] == session_id for s in list_resp.json()["sessions"])

        # Get session
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == session_id

        # Delete session
        del_resp = client.delete(f"/sessions/{session_id}")
        assert del_resp.status_code == 204

        # Verify deletion
        verify_resp = client.get(f"/sessions/{session_id}")
        assert verify_resp.status_code == 404


class TestSessionAgentName:
    """Test session creation with agent_name parameter."""

    def test_create_session_with_agent_name(self):
        """Test creating a session with explicit agent_name."""
        response = client.post(
            "/sessions",
            json={"title": "Agent Test Session", "agent_name": "readonly"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "readonly"

    def test_create_session_requires_agent_name(self):
        """Session creation rejects an omitted Agent binding."""
        response = client.post(
            "/sessions",
            json={"title": "Missing Agent Session"},
        )
        assert response.status_code == 422

    def test_create_session_invalid_agent_name(self):
        """Test creating session with unknown agent_name returns 422."""
        response = client.post(
            "/sessions",
            json={"title": "Invalid Agent Session", "agent_name": "nonexistent-agent"},
        )
        assert response.status_code == 422

    def test_create_session_with_scoped_api_agent(self):
        """Scoped API-created agents are valid with matching scope only."""
        settings = Settings(scoping_enabled=True, scope_keys=["tenant", "user"])
        app.dependency_overrides[get_settings_dep] = lambda: settings

        agent_name = f"scoped-agent-{uuid.uuid4().hex}"
        headers = {
            "X-Cognition-Scope-Tenant": "kennel-testing-lab",
            "X-Cognition-Scope-User": "surface-a",
        }
        other_headers = {
            "X-Cognition-Scope-Tenant": "kennel-testing-lab",
            "X-Cognition-Scope-User": "surface-b",
        }
        try:
            create_agent = client.post(
                "/agents",
                headers=headers,
                json={
                    "name": agent_name,
                    "system_prompt": "You are scoped.",
                    "mode": "primary",
                    "skills": [],
                },
            )
            assert create_agent.status_code == 201

            visible = client.get(f"/agents/{agent_name}", headers=headers)
            assert visible.status_code == 200

            hidden = client.get(f"/agents/{agent_name}", headers=other_headers)
            assert hidden.status_code == 404

            valid_session = client.post(
                "/sessions",
                headers=headers,
                json={"title": "Scoped Agent Session", "agent_name": agent_name},
            )
            assert valid_session.status_code == 201
            assert valid_session.json()["agent_name"] == agent_name

            invalid_session = client.post(
                "/sessions",
                headers=other_headers,
                json={"title": "Wrong Scope", "agent_name": agent_name},
            )
            assert invalid_session.status_code == 422
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)

    def test_update_session_agent_name_uses_scope(self):
        """PATCH /sessions validates agent_name in the request scope."""
        settings = Settings(scoping_enabled=True, scope_keys=["tenant", "user"])
        app.dependency_overrides[get_settings_dep] = lambda: settings

        first_agent = f"scoped-agent-{uuid.uuid4().hex}"
        second_agent = f"scoped-agent-{uuid.uuid4().hex}"
        headers = {
            "X-Cognition-Scope-Tenant": "kennel-testing-lab",
            "X-Cognition-Scope-User": "surface-a",
        }
        try:
            for agent_name in (first_agent, second_agent):
                create_agent = client.post(
                    "/agents",
                    headers=headers,
                    json={
                        "name": agent_name,
                        "system_prompt": "You are scoped.",
                        "mode": "primary",
                        "skills": [],
                    },
                )
                assert create_agent.status_code == 201

            create_session = client.post(
                "/sessions",
                headers=headers,
                json={"title": "Scoped Patch Session", "agent_name": first_agent},
            )
            assert create_session.status_code == 201
            session_id = create_session.json()["id"]

            patch_session = client.patch(
                f"/sessions/{session_id}",
                headers=headers,
                json={"agent_name": second_agent},
            )
            assert patch_session.status_code == 200
            assert patch_session.json()["agent_name"] == second_agent
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)

    def test_session_agent_name_persisted(self):
        """Test agent_name is persisted and returned in session details."""
        # Create session
        create_resp = client.post(
            "/sessions",
            json={"title": "Persisted Agent", "agent_name": "readonly"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Get session and verify agent_name
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["agent_name"] == "readonly"

    def test_create_session_accepts_scoped_primary_agent(self):
        """Scoped primary agents listed by /agents can be bound at session creation."""
        scoped_settings = Settings(scoping_enabled=True, scope_keys=["tenant", "user"])
        headers = {
            "X-Cognition-Scope-Tenant": "wasaloon",
            "X-Cognition-Scope-User": "kennel-surface-wa-salon-concierge",
        }
        agent_name = f"test-scoped-primary-{uuid.uuid4().hex}"
        app.dependency_overrides[get_settings_dep] = lambda: scoped_settings
        try:
            create_agent_resp = client.post(
                "/agents",
                json={
                    "name": agent_name,
                    "mode": "primary",
                    "hidden": False,
                    "system_prompt": "Scoped primary session test.",
                },
                headers=headers,
            )
            assert create_agent_resp.status_code == 201

            agents_resp = client.get("/agents", headers=headers)
            assert agents_resp.status_code == 200
            assert any(agent["name"] == agent_name for agent in agents_resp.json()["agents"])

            session_resp = client.post(
                "/sessions",
                json={
                    "agent_name": agent_name,
                    "title": "diagnostic-session-create",
                },
                headers=headers,
            )
            assert session_resp.status_code == 201
            assert session_resp.json()["agent_name"] == agent_name
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)

    def test_update_session_accepts_scoped_primary_agent(self):
        """Session agent_name updates validate primary agents in the request scope."""
        scoped_settings = Settings(scoping_enabled=True, scope_keys=["tenant", "user"])
        headers = {
            "X-Cognition-Scope-Tenant": "wasaloon",
            "X-Cognition-Scope-User": "kennel-surface-wa-salon-concierge",
        }
        agent_name = f"test-scoped-update-primary-{uuid.uuid4().hex}"
        app.dependency_overrides[get_settings_dep] = lambda: scoped_settings
        try:
            create_agent_resp = client.post(
                "/agents",
                json={
                    "name": agent_name,
                    "mode": "primary",
                    "hidden": False,
                    "system_prompt": "Scoped primary update test.",
                },
                headers=headers,
            )
            assert create_agent_resp.status_code == 201

            session_resp = client.post(
                "/sessions",
                json={"agent_name": "default", "title": "scoped-session-agent-update"},
                headers=headers,
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            update_resp = client.patch(
                f"/sessions/{session_id}",
                json={"agent_name": agent_name},
                headers=headers,
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["agent_name"] == agent_name
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)


class TestScopedSessionAgentName:
    """Test scoped API-created agent resolution for session binding."""

    def test_scoped_api_created_agent_can_create_and_update_sessions(self):
        """Session agent validation must use the same scope as the agent API."""
        scoped_settings = Settings(
            scoping_enabled=True,
            scope_keys=["tenant", "user"],
        )
        app.dependency_overrides[get_settings_dep] = lambda: scoped_settings
        name = f"kennel-testing-lab-lambda-microvm-smoke-{uuid.uuid4().hex[:8]}"
        headers = {
            "X-Cognition-Scope-Tenant": "kennel-testing-lab",
            "X-Cognition-Scope-User": "kennel-surface-lambda-microvm-smoke",
        }
        wrong_headers = {
            "X-Cognition-Scope-Tenant": "kennel-testing-lab",
            "X-Cognition-Scope-User": "other-user",
        }

        try:
            create_agent_resp = client.post(
                "/agents",
                headers=headers,
                json={
                    "name": name,
                    "description": "Kennel scoped Lambda MicroVM smoke agent",
                    "system_prompt": "You are a scoped smoke-test agent.",
                    "mode": "primary",
                    "sandbox_profile": "lambda-microvm-smoke",
                },
            )
            assert create_agent_resp.status_code == 201, create_agent_resp.text

            same_scope_get = client.get(f"/agents/{name}", headers=headers)
            assert same_scope_get.status_code == 200

            wrong_scope_get = client.get(f"/agents/{name}", headers=wrong_headers)
            assert wrong_scope_get.status_code == 404

            missing_scope_session = client.post(
                "/sessions",
                json={"title": "missing-scope", "agent_name": name},
            )
            assert missing_scope_session.status_code == 403

            wrong_scope_session = client.post(
                "/sessions",
                headers=wrong_headers,
                json={"title": "wrong-scope", "agent_name": name},
            )
            assert wrong_scope_session.status_code == 422

            create_session_resp = client.post(
                "/sessions",
                headers=headers,
                json={"title": "direct generated agent probe", "agent_name": name},
            )
            assert create_session_resp.status_code == 201, create_session_resp.text
            assert create_session_resp.json()["agent_name"] == name

            patch_target_resp = client.post(
                "/sessions",
                headers=headers,
                json={"agent_name": "default", "title": "patch generated agent probe"},
            )
            assert patch_target_resp.status_code == 201, patch_target_resp.text

            patch_session_resp = client.patch(
                f"/sessions/{patch_target_resp.json()['id']}",
                headers=headers,
                json={"agent_name": name},
            )
            assert patch_session_resp.status_code == 200, patch_session_resp.text
            assert patch_session_resp.json()["agent_name"] == name
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)
            client.delete(f"/agents/{name}", headers=headers)


class TestAgentEndpoints:
    """Test agent management API endpoints."""

    def test_list_agents(self):
        """Test listing agents endpoint."""
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    def test_list_agents_contains_builtins(self):
        """Test that built-in agents are in the list."""
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        agent_names = [a["name"] for a in data["agents"]]
        assert "default" in agent_names
        assert "readonly" in agent_names

    def test_list_agents_structure(self):
        """Test that agent list items have correct structure."""
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()

        for agent in data["agents"]:
            assert "name" in agent
            assert "description" in agent
            assert "mode" in agent
            assert "hidden" in agent
            assert "native" in agent

    def test_get_agent(self):
        """Test getting a specific agent."""
        response = client.get("/agents/default")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default"
        assert "description" in data
        assert "mode" in data

    def test_get_agent_not_found(self):
        """Test getting non-existent agent returns 404."""
        response = client.get("/agents/nonexistent-agent-12345")
        assert response.status_code == 404

    def test_get_agent_fields(self):
        """Test agent detail has all expected fields."""
        response = client.get("/agents/default")
        assert response.status_code == 200
        data = response.json()

        assert "name" in data
        assert "description" in data
        assert "mode" in data
        assert "hidden" in data
        assert "native" in data
        assert "model" in data
        assert "temperature" in data
