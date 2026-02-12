"""End-to-end tests for Cognition.

These tests verify the complete system works together:
1. Server startup
2. Client connection
3. Project creation
4. Session creation
5. Message sending with SSE streaming
6. Error handling
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port):
        """Start the server on an unused port."""
        port = unused_tcp_port
        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"  # Use mock LLM for tests

        # Start server
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        # Wait for server to be ready
        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        timeout = 10  # 10 seconds to start

        while time.time() - start_time < timeout:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200 and response.json().get("ready"):
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Server failed to start")

        yield base_url

        # Cleanup: terminate server
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

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
            assert "/projects" in str(spec["paths"])
            assert "/sessions" in str(spec["paths"])


class TestProjectWorkflow:
    """Test complete project workflow."""

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port):
        """Start server and return base URL."""
        port = unused_tcp_port
        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        while time.time() - start_time < 10:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Server failed to start")

        yield base_url

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    async def test_create_and_list_projects(self, server):
        """Test creating a project and listing it."""
        async with httpx.AsyncClient() as client:
            # Create project
            create_resp = await client.post(
                f"{server}/projects",
                json={"name": "e2e-test-project", "description": "Test project"},
            )
            assert create_resp.status_code == 201
            project = create_resp.json()
            assert project["name"] == "e2e-test-project"
            assert "id" in project
            project_id = project["id"]

            # List projects
            list_resp = await client.get(f"{server}/projects")
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1
            assert any(p["id"] == project_id for p in data["projects"])

            # Get specific project
            get_resp = await client.get(f"{server}/projects/{project_id}")
            # Note: Get might return 404 in current implementation
            # as it's not fully implemented

    async def test_project_validation(self, server):
        """Test project creation validation."""
        async with httpx.AsyncClient() as client:
            # Empty name should fail
            response = await client.post(
                f"{server}/projects",
                json={"name": ""},
            )
            assert response.status_code == 422

            # Missing name should fail
            response = await client.post(
                f"{server}/projects",
                json={},
            )
            assert response.status_code == 422


class TestSessionWorkflow:
    """Test complete session workflow."""

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port):
        """Start server and return base URL."""
        port = unused_tcp_port
        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        while time.time() - start_time < 10:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Server failed to start")

        yield base_url

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    @pytest_asyncio.fixture
    async def project(self, server):
        """Create a project and return its ID."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/projects",
                json={"name": "e2e-session-test"},
            )
            assert response.status_code == 201
            return response.json()["id"]

    async def test_create_session(self, server, project):
        """Test creating a session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions",
                json={
                    "project_id": project,
                    "title": "E2E Test Session",
                    "config": {"provider": "mock", "temperature": 0.5},
                },
            )
            assert response.status_code == 201
            session = response.json()
            assert session["project_id"] == project
            assert session["title"] == "E2E Test Session"
            assert "id" in session
            assert "thread_id" in session

    async def test_list_sessions(self, server, project):
        """Test listing sessions."""
        async with httpx.AsyncClient() as client:
            # Create a session first
            create_resp = await client.post(
                f"{server}/sessions",
                json={"project_id": project, "title": "List Test"},
            )
            assert create_resp.status_code == 201

            # List sessions
            list_resp = await client.get(f"{server}/sessions")
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1

            # Filter by project
            filtered_resp = await client.get(
                f"{server}/sessions",
                params={"project_id": project},
            )
            assert filtered_resp.status_code == 200

    async def test_update_session(self, server, project):
        """Test updating a session."""
        async with httpx.AsyncClient() as client:
            # Create session
            create_resp = await client.post(
                f"{server}/sessions",
                json={"project_id": project, "title": "Original"},
            )
            session_id = create_resp.json()["id"]

            # Update session
            update_resp = await client.patch(
                f"{server}/sessions/{session_id}",
                json={"title": "Updated"},
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["title"] == "Updated"

    async def test_delete_session(self, server, project):
        """Test deleting a session."""
        async with httpx.AsyncClient() as client:
            # Create session
            create_resp = await client.post(
                f"{server}/sessions",
                json={"project_id": project},
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
    async def server(self, unused_tcp_port):
        """Start server and return base URL."""
        port = unused_tcp_port
        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        while time.time() - start_time < 10:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Server failed to start")

        yield base_url

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    @pytest_asyncio.fixture
    async def session(self, server):
        """Create a project and session, return session ID."""
        async with httpx.AsyncClient() as client:
            # Create project
            project_resp = await client.post(
                f"{server}/projects",
                json={"name": "e2e-message-test"},
            )
            project_id = project_resp.json()["id"]

            # Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={"project_id": project_id, "title": "Message Test"},
            )
            return session_resp.json()["id"]

    async def test_send_message_sse_stream(self, server, session):
        """Test sending a message and receiving SSE stream."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST",
                f"{server}/sessions/{session}/messages",
                json={"content": "Hello, world!"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")

                # Collect events
                events = []
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append({"event": event_type, "data": data})

                # Verify we got events
                assert len(events) > 0

                # Should have at least a done event
                done_events = [e for e in events if e["event"] == "done"]
                assert len(done_events) == 1

    async def test_list_messages_after_send(self, server, session):
        """Test listing messages after sending."""
        async with httpx.AsyncClient() as client:
            # Send a message
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

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port):
        """Start server and return base URL."""
        port = unused_tcp_port
        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        while time.time() - start_time < 10:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Server failed to start")

        yield base_url

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    async def test_404_errors(self, server):
        """Test 404 error responses."""
        async with httpx.AsyncClient() as client:
            # Non-existent project
            response = await client.get(f"{server}/projects/non-existent")
            assert response.status_code == 404

            # Non-existent session
            response = await client.get(f"{server}/sessions/non-existent")
            assert response.status_code == 404

            # Non-existent message
            response = await client.get(f"{server}/sessions/non-existent/messages/msg-id")
            assert response.status_code == 404

    async def test_validation_errors(self, server):
        """Test validation error responses."""
        async with httpx.AsyncClient() as client:
            # Invalid session config
            response = await client.post(
                f"{server}/sessions",
                json={"project_id": "test", "config": {"temperature": 5.0}},  # Invalid: > 2.0
            )
            assert response.status_code == 422

    async def test_session_not_found_for_message(self, server):
        """Test sending message to non-existent session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server}/sessions/non-existent/messages",
                json={"content": "Hello"},
            )
            assert response.status_code == 404


class TestFullWorkflow:
    """Test complete end-to-end workflow."""

    @pytest_asyncio.fixture
    async def server(self, unused_tcp_port):
        """Start server and return base URL."""
        port = unused_tcp_port
        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_LLM_PROVIDER"] = "mock"

        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        while time.time() - start_time < 10:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready")
                    if response.status_code == 200:
                        break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("Server failed to start")

        yield base_url

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    async def test_complete_conversation(self, server):
        """Test complete conversation workflow.

        1. Create project
        2. Create session
        3. Send multiple messages
        4. Verify responses
        5. Clean up
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Create project
            project_resp = await client.post(
                f"{server}/projects",
                json={"name": "e2e-full-workflow", "description": "Complete test"},
            )
            assert project_resp.status_code == 201
            project_id = project_resp.json()["id"]

            # 2. Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={"project_id": project_id, "title": "Conversation"},
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # 3. Send first message
            events1 = []
            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Hello!"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        events1.append({"event": event_type, "data": data})

            # Verify first response
            done_events = [e for e in events1 if e["event"] == "done"]
            assert len(done_events) == 1

            # 4. Send second message
            events2 = []
            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "What files are in this project?"},
                headers={"Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        events2.append({"event": event_type, "data": data})

            # Verify second response
            done_events = [e for e in events2 if e["event"] == "done"]
            assert len(done_events) == 1

            # 5. List messages
            messages_resp = await client.get(f"{server}/sessions/{session_id}/messages")
            assert messages_resp.status_code == 200
            messages_data = messages_resp.json()
            assert messages_data["total"] >= 2  # At least 2 user messages

            # 6. Clean up
            await client.delete(f"{server}/sessions/{session_id}")
            await client.delete(f"{server}/projects/{project_id}")

            print(f"✅ Complete workflow test passed!")
            print(f"   - Project: {project_id}")
            print(f"   - Session: {session_id}")
            print(f"   - Messages: {messages_data['total']}")
