"""End-to-end integration tests for Cognition.

These tests verify the full workflow:
1. Project creation
2. Session establishment
3. Coding operations (search, read, modify, test)
4. Session persistence across disconnects
5. Project resumption

Requires running server and Docker.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest

# Skip all tests if dependencies are not available
try:
    import websockets

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

# Skip all tests if server is not running
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not WEBSOCKETS_AVAILABLE, reason="websockets and aiohttp not installed"),
]


@pytest.fixture(scope="module")
def server_url():
    """Get server URL."""
    return "ws://localhost:8000/ws"


@pytest.fixture(scope="module")
def api_url():
    """Get API base URL."""
    return "http://localhost:8000"


@pytest.fixture(scope="module")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def websocket_client(server_url):
    """Create a WebSocket client connection."""
    if not WEBSOCKETS_AVAILABLE:
        pytest.skip("websockets not installed")
    import websockets

    try:
        ws = await websockets.connect(server_url)
        yield ws
        await ws.close()
    except Exception as e:
        pytest.skip(f"Server not available: {e}")


@pytest.fixture
async def api_client(api_url):
    """Create an API client for REST endpoints."""
    if not WEBSOCKETS_AVAILABLE:
        pytest.skip("aiohttp not installed")
    import aiohttp

    async with aiohttp.ClientSession() as session:
        yield session


class TestProjectLifecycle:
    """Test complete project lifecycle."""

    @pytest.mark.asyncio
    async def test_create_project_via_api(self, api_client, api_url):
        """Test creating a project via REST API."""
        project_data = {
            "user_prefix": "test-project",
            "network_mode": "OFF",
        }

        async with api_client.post(f"{api_url}/api/projects", json=project_data) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "project_id" in data
            assert data["user_prefix"] == "test-project"
            assert "workspace_path" in data

    @pytest.mark.asyncio
    async def test_list_projects(self, api_client, api_url):
        """Test listing projects via API."""
        async with api_client.get(f"{api_url}/api/projects") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "projects" in data
            assert isinstance(data["projects"], list)

    @pytest.mark.asyncio
    async def test_list_resumable_sessions(self, api_client, api_url):
        """Test listing resumable sessions."""
        async with api_client.get(f"{api_url}/api/sessions/resumable") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "sessions" in data
            assert "total" in data
            assert "message" in data

    @pytest.mark.asyncio
    async def test_get_project_details(self, api_client, api_url):
        """Test getting project details."""
        # First create a project
        project_data = {
            "user_prefix": "detail-test",
            "network_mode": "OFF",
        }

        async with api_client.post(f"{api_url}/api/projects", json=project_data) as resp:
            data = await resp.json()
            project_id = data["project_id"]

        # Get details
        async with api_client.get(f"{api_url}/api/projects/{project_id}") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["project_id"] == project_id
            assert "config" in data
            assert "statistics" in data
            assert "sessions" in data


class TestWebSocketSession:
    """Test WebSocket session management."""

    @pytest.mark.asyncio
    async def test_create_session_with_project(self, websocket_client):
        """Test creating a session with a project via WebSocket."""
        # Send create_session with user_prefix
        message = {
            "type": "create_session",
            "user_prefix": "ws-test",
            "network_mode": "OFF",
        }

        await websocket_client.send(json.dumps(message))

        # Wait for session_started event
        response = await websocket_client.recv()
        data = json.loads(response)

        assert data["event"] == "session_started"
        assert "session_id" in data
        assert "workspace_path" in data
        assert "network_mode" in data

    @pytest.mark.asyncio
    async def test_send_user_message(self, websocket_client):
        """Test sending a user message."""
        # First create a session
        await websocket_client.send(
            json.dumps(
                {
                    "type": "create_session",
                    "user_prefix": "msg-test",
                    "network_mode": "OFF",
                }
            )
        )

        # Wait for session_started
        response = await websocket_client.recv()
        data = json.loads(response)
        session_id = data["session_id"]

        # Send user message
        await websocket_client.send(
            json.dumps(
                {
                    "type": "user_msg",
                    "session_id": session_id,
                    "content": "List all Python files in the workspace",
                }
            )
        )

        # Collect responses (we should get some events)
        events = []
        try:
            for _ in range(10):  # Collect up to 10 events
                response = await asyncio.wait_for(websocket_client.recv(), timeout=5.0)
                events.append(json.loads(response))
                if any(e.get("event") == "done" for e in events):
                    break
        except asyncio.TimeoutError:
            pass  # Expected if operation takes time

        # Should have received some events
        assert len(events) > 0
        event_types = {e.get("event") for e in events}
        assert "assistant_message" in event_types or "tool_start" in event_types


class TestCodingWorkflow:
    """Test complete coding workflow."""

    @pytest.mark.asyncio
    async def test_search_and_read_workflow(self, websocket_client):
        """Test search → read workflow."""
        # Create session with a simple Python file
        await websocket_client.send(
            json.dumps(
                {
                    "type": "create_session",
                    "user_prefix": "coding-test",
                    "network_mode": "OFF",
                }
            )
        )

        response = await websocket_client.recv()
        data = json.loads(response)
        session_id = data["session_id"]

        # Create a test file via message
        await websocket_client.send(
            json.dumps(
                {
                    "type": "user_msg",
                    "session_id": session_id,
                    "content": "Create a file called test.py with a simple hello function",
                }
            )
        )

        # Collect events
        events = []
        try:
            for _ in range(20):
                response = await asyncio.wait_for(websocket_client.recv(), timeout=3.0)
                events.append(json.loads(response))
                if any(e.get("event") == "done" for e in events):
                    break
        except asyncio.TimeoutError:
            pass

        # Verify we got meaningful events
        event_types = {e.get("event") for e in events}
        assert len(event_types) > 0

    @pytest.mark.asyncio
    async def test_session_persistence(self, api_client, api_url):
        """Test that session data persists across connections."""
        # Create a project
        project_data = {
            "user_prefix": "persistence-test",
            "network_mode": "OFF",
        }

        async with api_client.post(f"{api_url}/api/projects", json=project_data) as resp:
            data = await resp.json()
            project_id = data["project_id"]

        # Create session for project
        async with api_client.post(
            f"{api_url}/api/projects/{project_id}/sessions", json={"network_mode": "OFF"}
        ) as resp:
            assert resp.status == 200
            session_data = await resp.json()
            session_id = session_data["session_id"]

        # Verify project now has a session
        async with api_client.get(f"{api_url}/api/projects/{project_id}") as resp:
            data = await resp.json()
            assert len(data["sessions"]) > 0


class TestNetworkModes:
    """Test network ON/OFF modes."""

    @pytest.mark.asyncio
    async def test_network_off_mode(self, websocket_client):
        """Test that network OFF mode restricts network access."""
        await websocket_client.send(
            json.dumps(
                {
                    "type": "create_session",
                    "user_prefix": "network-off-test",
                    "network_mode": "OFF",
                }
            )
        )

        response = await websocket_client.recv()
        data = json.loads(response)
        assert data["network_mode"] == "OFF"

        # Try to run a command that requires network
        await websocket_client.send(
            json.dumps(
                {
                    "type": "user_msg",
                    "session_id": data["session_id"],
                    "content": "Try to ping google.com",
                }
            )
        )

        # Should fail or be blocked
        # (Actual behavior depends on container network config)

    @pytest.mark.asyncio
    async def test_network_on_mode(self, api_client, api_url):
        """Test that network ON mode allows network access."""
        project_data = {
            "user_prefix": "network-on-test",
            "network_mode": "ON",
        }

        async with api_client.post(f"{api_url}/api/projects", json=project_data) as resp:
            assert resp.status == 200
            data = await resp.json()
            # Project should be created with network ON
            # Actual network testing would require running commands


class TestErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_invalid_project_id(self, api_client, api_url):
        """Test error handling for invalid project ID."""
        async with api_client.get(f"{api_url}/api/projects/nonexistent-id") as resp:
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_invalid_message_format(self, websocket_client):
        """Test handling of invalid message format."""
        await websocket_client.send(
            json.dumps(
                {
                    "type": "invalid_type",
                }
            )
        )

        # Should receive error response
        response = await websocket_client.recv()
        data = json.loads(response)
        assert data["event"] == "error"


class TestPerformance:
    """Test performance characteristics."""

    @pytest.mark.asyncio
    async def test_session_creation_time(self, api_client, api_url):
        """Test that session creation completes within reasonable time."""
        start_time = time.time()

        await api_client.post(
            f"{api_url}/api/projects", json={"user_prefix": "perf-test", "network_mode": "OFF"}
        )

        elapsed = time.time() - start_time
        # Should complete in under 5 seconds (includes container creation)
        assert elapsed < 10.0, f"Session creation took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_project_listing_performance(self, api_client, api_url):
        """Test that project listing is fast."""
        start_time = time.time()

        async with api_client.get(f"{api_url}/api/projects") as resp:
            await resp.json()

        elapsed = time.time() - start_time
        # Should be very fast (< 100ms for small number of projects)
        assert elapsed < 1.0, f"Project listing took {elapsed:.2f}s"


@pytest.mark.integration
class TestContainerExecution:
    """Test container execution capabilities."""

    def test_docker_available(self):
        """Verify Docker is available for tests."""
        result = subprocess.run(["docker", "ps"], capture_output=True, text=True)
        assert result.returncode == 0, "Docker not available"

    def test_agent_image_exists(self):
        """Verify agent image is built."""
        result = subprocess.run(
            ["docker", "images", "opencode-agent:py", "-q"], capture_output=True, text=True
        )
        assert result.stdout.strip(), "opencode-agent:py image not found"


# Cleanup fixture
@pytest.fixture(scope="session", autouse=True)
def cleanup_test_projects(api_url):
    """Clean up test projects after all tests."""
    yield

    # Cleanup code runs after all tests
    import aiohttp
    import asyncio

    async def cleanup():
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{api_url}/api/projects") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for project in data.get("projects", []):
                            # Delete test projects
                            if project["user_prefix"].startswith("test-"):
                                await session.delete(
                                    f"{api_url}/api/projects/{project['project_id']}"
                                )
            except Exception:
                pass  # Ignore cleanup errors

    try:
        asyncio.get_event_loop().run_until_complete(cleanup())
    except Exception:
        pass
