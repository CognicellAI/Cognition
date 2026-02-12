"""Shared test fixtures and configuration."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Generator
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import pytest_asyncio


# ============================================================================
# PYTEST CONFIG & MARKERS
# ============================================================================


def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: mark test as a unit test (fast, no I/O)")
    config.addinivalue_line(
        "markers", "integration: mark test as integration (requires docker/setup)"
    )
    config.addinivalue_line("markers", "e2e: mark test as end-to-end (full stack test)")


# ============================================================================
# SHARED FIXTURES
# ============================================================================


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_settings():
    """Mock application settings."""
    settings = MagicMock()
    settings.host = "localhost"
    settings.port = 8000
    settings.ws_url = "ws://localhost:8000/ws"
    settings.base_url = "http://localhost:8000"
    settings.api_base = "http://localhost:8000/api"
    settings.server_host = "localhost"
    settings.server_port = 8000
    settings.default_network_mode = "OFF"
    settings.ws_reconnect_attempts = 5
    settings.ws_reconnect_delay = 1.0
    settings.ws_heartbeat_interval = 30.0
    return settings


# ============================================================================
# CLIENT-SPECIFIC FIXTURES
# ============================================================================


@pytest.fixture
def mock_app():
    """Mock Textual App instance."""
    app = MagicMock()
    app.post_message = MagicMock()
    app.notify = MagicMock()
    app.screen = MagicMock()
    return app


@pytest_asyncio.fixture
async def httpx_mock_client():
    """Mock httpx AsyncClient for API tests."""
    import httpx

    # Create a real httpx AsyncClient but it will use mocking
    client = httpx.AsyncClient()
    return client


@pytest.fixture
def sample_project_response() -> dict[str, Any]:
    """Sample API response for project creation."""
    return {
        "project_id": "test-project-abc123",
        "user_prefix": "test-project",
        "created_at": "2026-02-12T01:00:00Z",
        "workspace_path": "/workspaces/test-project-abc123/repo",
    }


@pytest.fixture
def sample_session_started_event() -> dict[str, Any]:
    """Sample session_started WebSocket event."""
    return {
        "event": "session_started",
        "session_id": "session-xyz789",
        "network_mode": "OFF",
        "workspace_path": "/workspaces/test-project-abc123/repo",
    }


@pytest.fixture
def sample_assistant_message_event() -> dict[str, Any]:
    """Sample assistant_message WebSocket event."""
    return {
        "event": "assistant_message",
        "session_id": "session-xyz789",
        "content": "I'll help you with that. Let me start by reading the file.",
    }


@pytest.fixture
def sample_tool_start_event() -> dict[str, Any]:
    """Sample tool_start WebSocket event."""
    return {
        "event": "tool_start",
        "session_id": "session-xyz789",
        "tool": "read_file",
        "input": {"path": "src/main.py"},
    }


@pytest.fixture
def sample_tool_output_event() -> dict[str, Any]:
    """Sample tool_output WebSocket event."""
    return {
        "event": "tool_output",
        "session_id": "session-xyz789",
        "stream": "stdout",
        "chunk": "def hello():\n    return 'world'\n",
    }


@pytest.fixture
def sample_tool_end_event() -> dict[str, Any]:
    """Sample tool_end WebSocket event."""
    return {
        "event": "tool_end",
        "session_id": "session-xyz789",
        "tool": "read_file",
        "exit_code": 0,
    }


@pytest.fixture
def sample_done_event() -> dict[str, Any]:
    """Sample done WebSocket event."""
    return {
        "event": "done",
        "session_id": "session-xyz789",
    }


# ============================================================================
# SERVER-SPECIFIC FIXTURES
# ============================================================================


@pytest.fixture
def mock_project_manager():
    """Mock ProjectManager."""
    manager = MagicMock()
    manager.create_project = MagicMock()
    manager.load_project = MagicMock()
    manager.list_projects = MagicMock(return_value=[])
    manager.update_last_accessed = MagicMock()
    manager.add_session_record = MagicMock()
    manager.end_session_record = MagicMock()
    return manager


@pytest.fixture
def mock_session_manager():
    """Mock SessionManager."""
    manager = MagicMock()
    manager.create_or_resume_session = AsyncMock()
    manager.disconnect_session = AsyncMock()
    manager.get_session = MagicMock()
    manager.attach_websocket = MagicMock()
    manager.detach_websocket = MagicMock()
    manager.send_to_agent = AsyncMock()
    return manager


@pytest.fixture
def mock_container_executor():
    """Mock ContainerExecutor."""
    executor = MagicMock()
    executor.create_container = MagicMock(return_value="container-123")
    executor.stop_container = MagicMock()
    executor.execute_command = AsyncMock()
    return executor


# ============================================================================
# E2E TEST FIXTURES
# ============================================================================


@pytest_asyncio.fixture
async def running_server():
    """Start a real server for E2E tests.

    This fixture starts the Cognition server on localhost:8000
    for integration/E2E testing.
    """
    from server.app.main import app
    from fastapi.testclient import TestClient

    # For E2E, we'll use a test client for sync operations
    # and manually manage async where needed
    client = TestClient(app)
    yield client


@pytest.fixture
def api_base_url() -> str:
    """Base URL for API calls in tests."""
    return "http://localhost:8000"


@pytest.fixture
def ws_url() -> str:
    """WebSocket URL for tests."""
    return "ws://localhost:8000/ws"


# ============================================================================
# ASYNC TEST HELPERS
# ============================================================================


@pytest_asyncio.fixture
async def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def assert_valid_project_response(response: dict[str, Any]) -> None:
    """Assert that a response is a valid project creation response."""
    assert "project_id" in response
    assert "user_prefix" in response
    assert "workspace_path" in response
    assert response["project_id"].count("-") > 0  # UUID format check


def assert_valid_websocket_event(event: dict[str, Any]) -> None:
    """Assert that a dict is a valid WebSocket event."""
    assert "event" in event
    assert isinstance(event["event"], str)
    # session_id is optional for some events
    if "session_id" in event:
        assert isinstance(event["session_id"], (str, type(None)))
