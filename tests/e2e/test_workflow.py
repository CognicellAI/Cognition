"""End-to-end tests for Cognition.

These tests verify the complete system works together:
1. Server startup
2. Client connection
3. Session creation (workspace-based)
4. Message sending with SSE streaming
5. Error handling
"""

import json

import httpx
import pytest
import pytest_asyncio

from tests.e2e.test_scenarios.conftest import is_terminal_stream_event

# Mark all tests in this file as e2e
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
    pytest.mark.timeout(60),  # 60 second timeout for e2e tests
]


class TestServerLifecycle:
    """Test server startup and shutdown."""

    async def test_server_starts_and_responds(self, server):
        """Test server starts and responds to health check."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{server}/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "version" in data

    async def test_openapi_docs_available(self, server):
        """Test OpenAPI docs are accessible."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{server}/docs")
            assert response.status_code == 200
            assert "Swagger" in response.text or "openapi" in response.text

            response = await client.get(f"{server}/openapi.json")
            assert response.status_code == 200
            spec = response.json()
            assert spec["openapi"].startswith("3.")
            assert "/sessions" in str(spec["paths"])
            assert "/sessions/{session_id}/messages" in str(spec["paths"])


class TestSessionWorkflow:
    """Test complete session workflow."""

    async def test_create_session(self, server, scope_headers):
        """Test creating a session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions",
                json={"title": "E2E Test Session"},
                headers=scope_headers,
            )
            assert response.status_code == 201
            session = response.json()
            assert session["title"] == "E2E Test Session"
            assert "id" in session
            assert "thread_id" in session

    async def test_list_sessions(self, server, scope_headers):
        """Test listing sessions."""
        async with httpx.AsyncClient() as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "List Test"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201

            list_resp = await client.get(
                f"{server}/sessions", headers=scope_headers
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1
            assert any(s["title"] == "List Test" for s in data["sessions"])

    async def test_update_session(self, server, scope_headers):
        """Test updating a session."""
        async with httpx.AsyncClient() as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "Original"},
                headers=scope_headers,
            )
            session_id = create_resp.json()["id"]

            update_resp = await client.patch(
                f"{server}/sessions/{session_id}",
                json={"title": "Updated"},
                headers=scope_headers,
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["title"] == "Updated"

    async def test_delete_session(self, server, scope_headers):
        """Test deleting a session."""
        async with httpx.AsyncClient() as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "Delete Test"},
                headers=scope_headers,
            )
            session_id = create_resp.json()["id"]

            delete_resp = await client.delete(
                f"{server}/sessions/{session_id}", headers=scope_headers
            )
            assert delete_resp.status_code == 204

            get_resp = await client.get(
                f"{server}/sessions/{session_id}", headers=scope_headers
            )
            assert get_resp.status_code == 404


class TestMessageWorkflow:
    """Test message sending with SSE streaming."""

    @pytest_asyncio.fixture
    async def session(self, server, scope_headers):
        """Create a session, return session ID."""
        async with httpx.AsyncClient() as client:
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "Message Test"},
                headers=scope_headers,
            )
            return session_resp.json()["id"]

    async def test_send_message_sse_stream(self, server, session, scope_headers):
        """Test sending a message and receiving SSE stream."""
        async with (
            httpx.AsyncClient(timeout=30.0) as client,
            client.stream(
                "POST",
                f"{server}/sessions/{session}/messages",
                json={"content": "Hello, world!"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as response,
        ):
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            events = []
            event_type = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if event_type:
                        event = {"event": event_type, "data": data}
                        events.append(event)
                        if is_terminal_stream_event({"event": event_type, **data}):
                            break

            assert len(events) > 0
            terminal_events = [
                e for e in events if is_terminal_stream_event({"event": e["event"], **e["data"]})
            ]
            assert len(terminal_events) == 1

    async def test_list_messages_after_send(self, server, session, scope_headers):
        """Test listing messages after sending."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"{server}/sessions/{session}/messages",
                json={"content": "Test message"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as response:
                async for _ in response.aiter_lines():
                    pass

            list_resp = await client.get(
                f"{server}/sessions/{session}/messages", headers=scope_headers
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1
            assert len(data["messages"]) >= 1


class TestErrorHandling:
    """Test error handling scenarios."""

    async def test_404_errors(self, server):
        """Test 404 error handling."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{server}/sessions/non-existent-id")
            assert response.status_code in {403, 404}

    async def test_validation_errors(self, server, scope_headers):
        """Test validation error handling."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions",
                json={"title": "x" * 201},
                headers=scope_headers,
            )
            assert response.status_code == 422

    async def test_session_not_found_for_message(self, server, scope_headers):
        """Test error when sending message to non-existent session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions/non-existent/messages",
                json={"content": "Test"},
                headers=scope_headers,
            )
            assert response.status_code == 404


class TestFullWorkflow:
    """Test complete end-to-end workflow."""

    async def test_complete_conversation(self, server, scope_headers):
        """Test a complete conversation workflow."""
        async with httpx.AsyncClient() as client:
            session_resp = await client.post(
                f"{server}/sessions",
                json={
                    "title": "Complete Test",
                    "config": {"provider": "mock", "temperature": 0.7},
                },
                headers=scope_headers,
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            list_resp = await client.get(
                f"{server}/sessions", headers=scope_headers
            )
            assert list_resp.status_code == 200

            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Hello!"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200

                events = []
                event_type = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        if event_type:
                            event = {"event": event_type, "data": data}
                            events.append(event)
                            if is_terminal_stream_event({"event": event_type, **data}):
                                break

                terminal_events = [
                    e
                    for e in events
                    if is_terminal_stream_event({"event": e["event"], **e["data"]})
                ]
                assert len(terminal_events) == 1

            messages_resp = await client.get(
                f"{server}/sessions/{session_id}/messages", headers=scope_headers
            )
            assert messages_resp.status_code == 200
            data = messages_resp.json()
            assert data["total"] >= 1

            delete_resp = await client.delete(
                f"{server}/sessions/{session_id}", headers=scope_headers
            )
            assert delete_resp.status_code == 204

            get_resp = await client.get(
                f"{server}/sessions/{session_id}", headers=scope_headers
            )
            assert get_resp.status_code == 404
