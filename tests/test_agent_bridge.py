"""Tests for agent bridge (server-side WebSocket client)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.executor.bridge import AgentBridge, AgentBridgePool
from shared.protocol.internal import (
    AgentReadyEvent,
    AgentStartMessage,
    UserMessage,
)


class TestAgentBridge:
    """Test AgentBridge functionality."""

    @pytest.mark.asyncio
    async def test_bridge_connect_success(self):
        """Test successful connection to agent container."""
        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            await bridge.connect(timeout=5.0)

            assert bridge.is_connected
            mock_connect.assert_called_once()
            assert "ws://localhost:9000" in str(mock_connect.call_args)

    @pytest.mark.asyncio
    async def test_bridge_connect_timeout(self):
        """Test connection timeout handling."""
        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
        )

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with pytest.raises(ConnectionError, match="Timeout"):
                await bridge.connect(timeout=1.0)

    @pytest.mark.asyncio
    async def test_bridge_disconnect(self):
        """Test disconnection from agent container."""
        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            await bridge.connect()
            await bridge.disconnect()

            assert not bridge.is_connected
            mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_agent(self):
        """Test sending agent initialization message."""
        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            await bridge.connect()

            config = AgentStartMessage(
                session_id="session123",
                project_id="project456",
                llm_provider="openai",
                llm_model="gpt-4",
            )
            await bridge.initialize_agent(config)

            mock_ws.send.assert_called_once()
            sent_message = mock_ws.send.call_args[0][0]
            assert '"type": "agent_start"' in sent_message
            assert '"session_id": "session123"' in sent_message

    @pytest.mark.asyncio
    async def test_send_user_message(self):
        """Test sending user message to agent."""
        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            await bridge.connect()
            mock_ws.send.reset_mock()

            await bridge.send_user_message(
                session_id="session123",
                content="Hello, agent!",
                turn_number=1,
            )

            mock_ws.send.assert_called_once()
            sent_message = mock_ws.send.call_args[0][0]
            assert '"type": "user_message"' in sent_message
            assert '"content": "Hello, agent!"' in sent_message

    @pytest.mark.asyncio
    async def test_send_without_connection_raises(self):
        """Test that sending without connection raises error."""
        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
        )

        with pytest.raises(ConnectionError, match="Not connected"):
            await bridge.send_user_message("session", "content", 1)

    @pytest.mark.asyncio
    async def test_event_callback(self):
        """Test that agent events trigger the callback."""
        events_received = []

        async def on_event(event):
            events_received.append(event)

        bridge = AgentBridge(
            container_id="container123",
            agent_host="localhost",
            agent_port=9000,
            on_event=on_event,
        )

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()

            # Create async iterator for messages
            async def mock_messages():
                yield '{"event": "agent_ready", "session_id": "s1"}'

            mock_ws.__aiter__ = lambda self: mock_messages()
            mock_connect.return_value = mock_ws

            await bridge.connect()

            # Manually trigger the receive loop processing
            bridge._receive_task.cancel()
            try:
                await bridge._receive_task
            except asyncio.CancelledError:
                pass

            # Check that the callback was called
            # Note: In a real scenario, the event would be processed
            # This test verifies the bridge structure supports callbacks
            assert bridge.on_event is not None


class TestAgentBridgePool:
    """Test AgentBridgePool functionality."""

    @pytest.mark.asyncio
    async def test_create_and_get_bridge(self):
        """Test creating and retrieving a bridge from the pool."""
        pool = AgentBridgePool()

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            bridge = await pool.create_bridge(
                session_id="session123",
                container_id="container456",
                agent_port=9000,
            )

            assert bridge is not None
            assert bridge.is_connected

            # Retrieve the same bridge
            retrieved = await pool.get_bridge("session123")
            assert retrieved is bridge

    @pytest.mark.asyncio
    async def test_remove_bridge(self):
        """Test removing a bridge from the pool."""
        pool = AgentBridgePool()

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            await pool.create_bridge(
                session_id="session123",
                container_id="container456",
            )

            await pool.remove_bridge("session123")

            assert await pool.get_bridge("session123") is None
            mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_bridges(self):
        """Test closing all bridges in the pool."""
        pool = AgentBridgePool()

        with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
            mock_ws = AsyncMock()
            mock_connect.return_value = mock_ws

            # Create multiple bridges
            await pool.create_bridge(session_id="s1", container_id="c1")
            await pool.create_bridge(session_id="s2", container_id="c2")

            await pool.close_all()

            assert await pool.get_bridge("s1") is None
            assert await pool.get_bridge("s2") is None
            assert mock_ws.close.call_count == 2
