"""Unit tests for WebSocket client."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from client.tui.websocket import (
    AssistantMessageEvent,
    ConnectionState,
    DoneEvent,
    ErrorEvent,
    ServerEvent,
    ToolEndEvent,
    ToolOutputEvent,
    ToolStartEvent,
    WebSocketClient,
    WebSocketMessages,
)


@pytest.mark.unit
class TestWebSocketClient:
    """Test WebSocketClient class."""

    @pytest.fixture
    def mock_app(self):
        """Mock Textual App instance."""
        app = MagicMock()
        app.post_message = MagicMock()
        return app

    @pytest.fixture
    def ws_client(self, mock_app):
        """Create a WebSocketClient instance for testing."""
        return WebSocketClient(mock_app)

    def test_initialization(self, ws_client, mock_app):
        """Test WebSocketClient initializes correctly."""
        assert ws_client.app is mock_app
        assert ws_client.state == ConnectionState.DISCONNECTED
        assert ws_client.ws is None
        assert ws_client.is_connected is False

    def test_is_connected_property(self, ws_client):
        """Test is_connected property."""
        assert ws_client.is_connected is False

        ws_client.state = ConnectionState.CONNECTED
        ws_client.ws = MagicMock()
        assert ws_client.is_connected is True

        ws_client.state = ConnectionState.CONNECTING
        assert ws_client.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_success(self, ws_client, mock_app):
        """Test successful WebSocket connection."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())

        with patch(
            "client.tui.websocket.websockets.connect", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.return_value = mock_ws

            await ws_client.connect()

            assert ws_client.state == ConnectionState.CONNECTED
            assert ws_client.ws is mock_ws
            assert ws_client._reconnect_attempt == 0
            mock_app.post_message.assert_called()

    @pytest.mark.asyncio
    async def test_connect_already_connected(self, ws_client):
        """Test connect when already connected."""
        ws_client.state = ConnectionState.CONNECTED
        ws_client.ws = MagicMock()

        # Should return early without reconnecting
        await ws_client.connect()

        assert ws_client.state == ConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_connect_failure(self, ws_client):
        """Test connection failure."""
        with patch(
            "client.tui.websocket.websockets.connect", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = ConnectionError("Network unreachable")

            with pytest.raises(ConnectionError):
                await ws_client.connect()

            assert ws_client.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_disconnect_temporary(self, ws_client, mock_app):
        """Test temporary disconnection (allows reconnection)."""
        ws_client.state = ConnectionState.CONNECTED
        mock_ws = AsyncMock()
        ws_client.ws = mock_ws

        # Create real async tasks that we can cancel
        async def dummy_receive():
            await asyncio.sleep(10)

        async def dummy_heartbeat():
            await asyncio.sleep(10)

        ws_client._receive_task = asyncio.create_task(dummy_receive())
        ws_client._heartbeat_task = asyncio.create_task(dummy_heartbeat())

        await ws_client.disconnect(permanent=False)

        assert ws_client.state == ConnectionState.DISCONNECTED
        assert ws_client.ws is None
        assert ws_client._stop_requested is False
        # Check that Disconnected message was posted
        calls = [
            call
            for call in mock_app.post_message.call_args_list
            if isinstance(call[0][0], WebSocketMessages.Disconnected)
        ]
        assert len(calls) > 0
        assert calls[0][0][0].permanent is False

    @pytest.mark.asyncio
    async def test_disconnect_permanent(self, ws_client, mock_app):
        """Test permanent disconnection (prevents reconnection)."""
        ws_client.state = ConnectionState.CONNECTED
        mock_ws = AsyncMock()
        ws_client.ws = mock_ws

        # Create real async tasks that we can cancel
        async def dummy_receive():
            await asyncio.sleep(10)

        async def dummy_heartbeat():
            await asyncio.sleep(10)

        ws_client._receive_task = asyncio.create_task(dummy_receive())
        ws_client._heartbeat_task = asyncio.create_task(dummy_heartbeat())

        await ws_client.disconnect(permanent=True)

        assert ws_client.state == ConnectionState.DISCONNECTED
        assert ws_client._stop_requested is True
        # Check permanent flag
        calls = [
            call
            for call in mock_app.post_message.call_args_list
            if isinstance(call[0][0], WebSocketMessages.Disconnected)
        ]
        assert len(calls) > 0
        assert calls[0][0][0].permanent is True

    @pytest.mark.asyncio
    async def test_send_success(self, ws_client):
        """Test sending a message."""
        ws_client.state = ConnectionState.CONNECTED
        mock_ws = AsyncMock()
        ws_client.ws = mock_ws

        message = {"event": "user_message", "content": "Hello"}
        await ws_client.send(message)

        mock_ws.send.assert_called_once()
        sent_data = json.loads(mock_ws.send.call_args[0][0])
        assert sent_data["event"] == "user_message"
        assert sent_data["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_send_not_connected(self, ws_client):
        """Test sending when not connected."""
        ws_client.state = ConnectionState.DISCONNECTED
        ws_client.ws = None

        with pytest.raises(ConnectionError):
            await ws_client.send({"event": "test"})

    def test_parse_event_session_started(self, ws_client):
        """Test parsing session_started event."""
        data = {
            "event": "session_started",
            "session_id": "session-123",
            "network_mode": "OFF",
            "workspace_path": "/workspace",
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, ServerEvent)
        assert event.event_type == "session_started"
        assert event.session_id == "session-123"

    def test_parse_event_assistant_message(self, ws_client):
        """Test parsing assistant_message event."""
        data = {
            "event": "assistant_message",
            "session_id": "session-123",
            "content": "I'll help you.",
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, AssistantMessageEvent)
        assert event.content == "I'll help you."

    def test_parse_event_tool_start(self, ws_client):
        """Test parsing tool_start event."""
        data = {
            "event": "tool_start",
            "session_id": "session-123",
            "tool": "read_file",
            "input": {"path": "test.py"},
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, ToolStartEvent)
        assert event.tool == "read_file"
        assert event.tool_input == {"path": "test.py"}

    def test_parse_event_tool_output(self, ws_client):
        """Test parsing tool_output event."""
        data = {
            "event": "tool_output",
            "session_id": "session-123",
            "stream": "stdout",
            "chunk": "Output line",
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, ToolOutputEvent)
        assert event.stream == "stdout"
        assert event.chunk == "Output line"

    def test_parse_event_tool_end(self, ws_client):
        """Test parsing tool_end event."""
        data = {
            "event": "tool_end",
            "session_id": "session-123",
            "tool": "read_file",
            "exit_code": 0,
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, ToolEndEvent)
        assert event.exit_code == 0

    def test_parse_event_error(self, ws_client):
        """Test parsing error event."""
        data = {
            "event": "error",
            "session_id": "session-123",
            "message": "Something went wrong",
            "code": "TOOL_FAILED",
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, ErrorEvent)
        assert event.message == "Something went wrong"
        assert event.code == "TOOL_FAILED"

    def test_parse_event_done(self, ws_client):
        """Test parsing done event."""
        data = {
            "event": "done",
            "session_id": "session-123",
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, DoneEvent)
        assert event.event_type == "done"

    def test_parse_event_unknown(self, ws_client):
        """Test parsing unknown event type."""
        data = {
            "event": "unknown_event",
            "session_id": "session-123",
        }

        event = ws_client._parse_event(data)

        assert isinstance(event, ServerEvent)
        assert event.event_type == "unknown_event"

    def test_parse_event_missing_session_id(self, ws_client):
        """Test parsing event without session_id."""
        data = {
            "event": "assistant_message",
            "content": "Message",
        }

        event = ws_client._parse_event(data)

        assert event.session_id is None
        assert isinstance(event, AssistantMessageEvent)


@pytest.mark.unit
class TestConnectionState:
    """Test ConnectionState enum."""

    def test_connection_states(self):
        """Test all connection states are defined."""
        assert hasattr(ConnectionState, "DISCONNECTED")
        assert hasattr(ConnectionState, "CONNECTING")
        assert hasattr(ConnectionState, "CONNECTED")
        assert hasattr(ConnectionState, "RECONNECTING")


@pytest.mark.unit
class TestWebSocketMessages:
    """Test WebSocketMessages Textual message classes."""

    def test_connected_message(self):
        """Test Connected message."""
        msg = WebSocketMessages.Connected()
        assert isinstance(msg, WebSocketMessages.Connected)

    def test_disconnected_message(self):
        """Test Disconnected message with permanent flag."""
        msg = WebSocketMessages.Disconnected(permanent=True)
        assert msg.permanent is True

    def test_reconnecting_message(self):
        """Test Reconnecting message."""
        msg = WebSocketMessages.Reconnecting(attempt=2, max_attempts=5, delay=2.0)
        assert msg.attempt == 2
        assert msg.max_attempts == 5
        assert msg.delay == 2.0

    def test_event_received_message(self):
        """Test EventReceived message."""
        event = ServerEvent("test", "session-123", {})
        msg = WebSocketMessages.EventReceived(event)
        assert msg.event is event
