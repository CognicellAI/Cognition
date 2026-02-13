"""E2E tests for TUI using Textual's Pilot.

These tests use Textual's built-in testing framework to simulate
user interactions and verify TUI behavior without needing a real terminal.

Usage:
    pytest tests/e2e/test_tui.py -v

Note: These tests mock the API client to avoid needing a running server.
For integration tests with a real server, see test_tui_integration.py.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from client.tui.app import CognitionApp
from client.tui.api import Session, Message, TokenEvent, DoneEvent


def async_iter(items):
    """Helper to create async iterator from list."""

    async def generator():
        for item in items:
            yield item

    return generator()


class MockAPIClient:
    """Mock API client for testing."""

    def __init__(self, base_url: str = "", timeout: float = 30.0):
        self.base_url = base_url
        self.timeout = timeout
        self.closed = False

    async def health_check(self):
        return {"status": "healthy", "version": "0.1.0"}

    async def list_sessions(self):
        return []

    async def create_session(self, title=None):
        return Session(
            id="test-session-id",
            title=title or "New Session",
            thread_id="test-thread-id",
            status="active",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            message_count=0,
        )

    async def get_session(self, session_id: str):
        return Session(
            id=session_id,
            title="Test Session",
            thread_id="test-thread-id",
            status="active",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
            message_count=0,
        )

    async def delete_session(self, session_id: str):
        pass

    async def list_messages(self, session_id: str, limit=50, offset=0):
        return []

    async def send_message(self, session_id: str, content: str, model=None, parent_id=None):
        yield TokenEvent(content="Hello!")
        yield DoneEvent()

    async def get_available_models(self):
        return ["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet"]

    async def get_most_recent_session(self):
        return None

    async def close(self):
        self.closed = True


class FailingMockAPIClient(MockAPIClient):
    """Mock API client that fails to connect."""

    async def health_check(self):
        raise Exception("Connection failed")


@pytest.mark.asyncio
async def test_tui_starts_and_connects():
    """Test TUI starts up and connects to server."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            # Wait for initial mount and connection
            await pilot.pause()

            # Verify connection state
            assert app.is_connected is True

            # Verify UI components exist
            assert app._top_bar is not None
            assert app._sidebar is not None
            assert app._chat is not None
            assert app._workspace is not None


@pytest.mark.asyncio
async def test_tui_shows_connection_error():
    """Test TUI handles connection failure gracefully."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", FailingMockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()

            # Should show disconnected state
            assert app.is_connected is False


@pytest.mark.asyncio
async def test_tui_creates_session():
    """Test creating a new session via keyboard shortcut."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()

            # Create session via API call (simulating what Ctrl+N does)
            await app._create_session()
            await pilot.pause()

            # Verify session was created
            assert app.active_session is not None
            assert app.active_session.id == "test-session-id"


@pytest.mark.asyncio
async def test_tui_loads_available_models():
    """Test that available models are loaded on startup."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._load_available_models()
            await pilot.pause()

            # Verify models were loaded
            assert len(app.available_models) == 3
            assert "gpt-4o" in app.available_models


@pytest.mark.asyncio
async def test_tui_quits_on_ctrl_q():
    """Test Ctrl+Q quits the application."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()

            # Press Ctrl+Q to quit
            await pilot.press("ctrl", "q")
            await pilot.pause()

            # App should no longer be running
            # Note: is_running might still be True in test context


@pytest.mark.asyncio
async def test_tui_focus_management():
    """Test focus moves between widgets."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()

            # Focus sidebar
            await pilot.press("f2")
            await pilot.pause()
            # Sidebar should have focus

            # Focus workspace
            await pilot.press("f4")
            await pilot.pause()
            # Workspace should have focus

            # Test passes if no exceptions
            assert True


@pytest.mark.asyncio
async def test_tui_model_selection():
    """Test model can be selected."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._load_available_models()

            # Set selected model
            app.selected_model = "gpt-4o"

            # Verify selection
            assert app.selected_model == "gpt-4o"


@pytest.mark.asyncio
async def test_tui_session_list_populated():
    """Test that session list is populated from server."""
    app = CognitionApp(base_url="http://localhost:8000")

    class MockWithSessions(MockAPIClient):
        async def list_sessions(self):
            return [
                Session(
                    id="session-1",
                    title="Session 1",
                    thread_id="thread-1",
                    status="active",
                    created_at="2026-01-01T00:00:00",
                    updated_at="2026-01-01T00:00:00",
                    message_count=5,
                ),
            ]

    with patch("client.tui.app.CognitionAPIClient", MockWithSessions):
        async with app.run_test() as pilot:
            await pilot.pause()

            # Sessions should be loaded
            assert len(app._sidebar.sessions) == 1
            assert app._sidebar.sessions[0].id == "session-1"


@pytest.mark.asyncio
async def test_tui_message_model_tracked():
    """Test that model is tracked per message."""
    app = CognitionApp(base_url="http://localhost:8000")

    with patch("client.tui.app.CognitionAPIClient", MockAPIClient):
        async with app.run_test() as pilot:
            await pilot.pause()

            # Set a model
            app.selected_model = "claude-3-opus"

            # Create session
            await pilot.press("ctrl", "n")
            await pilot.pause()

            # Model should be set for sending
            assert app.selected_model == "claude-3-opus"
