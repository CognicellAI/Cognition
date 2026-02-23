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
            # Swagger UI
            response = await client.get(f"{server}/docs")
            assert response.status_code == 200
            assert "Swagger" in response.text or "openapi" in response.text

            # OpenAPI JSON
            response = await client.get(f"{server}/openapi.json")
            assert response.status_code == 200
            spec = response.json()
            assert spec["openapi"].startswith("3.")
            # No more projects endpoint - sessions are workspace-based
            assert "/sessions" in str(spec["paths"])
            assert "/sessions/{session_id}/messages" in str(spec["paths"])


class TestSessionWorkflow:
    """Test complete session workflow."""

    async def test_create_session(self, server):
        """Test creating a session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions",
                json={
                    "title": "E2E Test Session",
                },
            )
            assert response.status_code == 201
            session = response.json()
            assert session["title"] == "E2E Test Session"
            assert "id" in session
            assert "thread_id" in session
            # Note: No workspace_path or config (server uses global settings)

    async def test_list_sessions(self, server):
        """Test listing sessions."""
        async with httpx.AsyncClient() as client:
            # Create a session first
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "List Test"},
            )
            assert create_resp.status_code == 201

            # List sessions
            list_resp = await client.get(f"{server}/sessions")
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1
            # Check session exists by title
            assert any(s["title"] == "List Test" for s in data["sessions"])

    async def test_update_session(self, server):
        """Test updating a session."""
        async with httpx.AsyncClient() as client:
            # Create session
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "Original"},
            )
            session_id = create_resp.json()["id"]

            # Update session
            update_resp = await client.patch(
                f"{server}/sessions/{session_id}",
                json={"title": "Updated"},
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["title"] == "Updated"

    async def test_delete_session(self, server):
        """Test deleting a session."""
        async with httpx.AsyncClient() as client:
            # Create session
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "Delete Test"},
            )
            session_id = create_resp.json()["id"]

            # Delete session
            delete_resp = await client.delete(f"{server}/sessions/{session_id}")
            assert delete_resp.status_code == 204

            # Verify it's gone
            get_resp = await client.get(f"{server}/sessions/{session_id}")
            assert get_resp.status_code == 404


class TestMessageWorkflow:
    """Test message sending with SSE streaming."""

    @pytest_asyncio.fixture
    async def session(self, server):
        """Create a session, return session ID."""
        async with httpx.AsyncClient() as client:
            # Create session directly (no project needed)
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "Message Test"},
            )
            return session_resp.json()["id"]

    async def test_send_message_sse_stream(self, server, session):
        """Test sending a message and receiving SSE stream."""
        async with httpx.AsyncClient(timeout=30.0) as client, client.stream(
            "POST",
            f"{server}/sessions/{session}/messages",
            json={"content": "Hello, world!"},
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            # Collect events
            events = []
            event_type = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if event_type:
                        events.append({"event": event_type, "data": data})

            # Verify we got events
            assert len(events) > 0

            # Should have at least a done event
            done_events = [e for e in events if e["event"] == "done"]
            assert len(done_events) == 1

    async def test_list_messages_after_send(self, server, session):
        """Test listing messages after sending."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Send a message via SSE stream
            async with client.stream(
                "POST",
                f"{server}/sessions/{session}/messages",
                json={"content": "Test message"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                # Consume the stream
                async for _ in response.aiter_lines():
                    pass

            # List messages
            list_resp = await client.get(f"{server}/sessions/{session}/messages")
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1
            assert len(data["messages"]) >= 1


class TestErrorHandling:
    """Test error handling scenarios."""

    async def test_404_errors(self, server):
        """Test 404 error handling."""
        async with httpx.AsyncClient() as client:
            # Non-existent session
            response = await client.get(f"{server}/sessions/non-existent-id")
            assert response.status_code == 404

    async def test_validation_errors(self, server):
        """Test validation error handling."""
        async with httpx.AsyncClient() as client:
            # Title too long
            response = await client.post(
                f"{server}/sessions",
                json={"title": "x" * 201},
            )
            assert response.status_code == 422

    async def test_session_not_found_for_message(self, server):
        """Test error when sending message to non-existent session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions/non-existent/messages",
                json={"content": "Test"},
            )
            assert response.status_code == 404


class TestFullWorkflow:
    """Test complete end-to-end workflow."""

    async def test_complete_conversation(self, server):
        """Test a complete conversation workflow."""
        async with httpx.AsyncClient() as client:
            # Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={
                    "title": "Complete Test",
                    "config": {"provider": "mock", "temperature": 0.7},
                },
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # List sessions
            list_resp = await client.get(f"{server}/sessions")
            assert list_resp.status_code == 200

            # Send a message
            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Hello!"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200

                # Collect events
                events = []
                event_type = None
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        if event_type:
                            events.append({"event": event_type, "data": data})

                # Verify stream completed
                done_events = [e for e in events if e["event"] == "done"]
                assert len(done_events) == 1

            # List messages
            messages_resp = await client.get(f"{server}/sessions/{session_id}/messages")
            assert messages_resp.status_code == 200
            data = messages_resp.json()
            assert data["total"] >= 1

            # Clean up - delete session
            delete_resp = await client.delete(f"{server}/sessions/{session_id}")
            assert delete_resp.status_code == 204

            # Verify deletion
            get_resp = await client.get(f"{server}/sessions/{session_id}")
            assert get_resp.status_code == 404
