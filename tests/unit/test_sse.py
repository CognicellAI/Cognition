"""Unit tests for SSE reconnection support.

Tests for P2-1: SSE Reconnection with Last-Event-ID support.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from server.app.api.sse import (
    EventBuffer,
    EventBuilder,
    SSEEvent,
    SSEStream,
    get_last_event_id,
)
from server.app.settings import Settings


class TestSSEEvent:
    """Tests for SSEEvent dataclass."""

    def test_format_basic_event(self) -> None:
        """Test formatting a basic event."""
        event = SSEEvent(event_type="token", data={"content": "hello"})
        formatted = event.format()
        assert "event: token" in formatted
        assert 'data: {"content": "hello"}' in formatted
        assert formatted.endswith("\n\n")

    def test_format_with_event_id(self) -> None:
        """Test formatting event with ID."""
        event = SSEEvent(event_type="token", data={"content": "hello"}, event_id="123-abc")
        formatted = event.format()
        assert "id: 123-abc" in formatted
        assert "event: token" in formatted

    def test_format_with_retry(self) -> None:
        """Test formatting event with retry directive."""
        event = SSEEvent(event_type="token", data={"content": "hello"}, retry_ms=5000)
        formatted = event.format()
        assert "retry: 5000" in formatted
        assert "event: token" in formatted

    def test_format_full_event(self) -> None:
        """Test formatting event with all fields."""
        event = SSEEvent(
            event_type="tool_call",
            data={"name": "test"},
            event_id="456-def",
            retry_ms=3000,
        )
        formatted = event.format()
        assert "id: 456-def" in formatted
        assert "retry: 3000" in formatted
        assert "event: tool_call" in formatted
        assert '"name": "test"' in formatted


class TestEventBuffer:
    """Tests for EventBuffer class."""

    @pytest.mark.asyncio
    async def test_add_event(self) -> None:
        """Test adding event to buffer."""
        buffer = EventBuffer(max_size=10)
        await buffer.add("event-1", "token", {"content": "hello"})

        events = await buffer.get_events_after("")
        assert len(events) == 1
        assert events[0].event_id == "event-1"
        assert events[0].event_type == "token"

    @pytest.mark.asyncio
    async def test_buffer_size_limit(self) -> None:
        """Test buffer respects max size."""
        buffer = EventBuffer(max_size=3)

        # Add 5 events
        for i in range(5):
            await buffer.add(f"event-{i}", "token", {"content": str(i)})

        events = await buffer.get_events_after("")
        assert len(events) == 3  # Only last 3 should be kept
        assert events[0].event_id == "event-2"
        assert events[-1].event_id == "event-4"

    @pytest.mark.asyncio
    async def test_get_events_after(self) -> None:
        """Test retrieving events after specific ID."""
        buffer = EventBuffer(max_size=10)

        await buffer.add("event-1", "token", {"content": "1"})
        await buffer.add("event-2", "token", {"content": "2"})
        await buffer.add("event-3", "token", {"content": "3"})
        await buffer.add("event-4", "token", {"content": "4"})

        events = await buffer.get_events_after("event-2")
        assert len(events) == 2
        assert events[0].event_id == "event-3"
        assert events[1].event_id == "event-4"

    @pytest.mark.asyncio
    async def test_get_events_after_not_found(self) -> None:
        """Test retrieving events when ID not found returns all events."""
        buffer = EventBuffer(max_size=10)

        await buffer.add("event-1", "token", {"content": "1"})
        await buffer.add("event-2", "token", {"content": "2"})

        events = await buffer.get_events_after("event-unknown")
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_clear_buffer(self) -> None:
        """Test clearing buffer."""
        buffer = EventBuffer(max_size=10)

        await buffer.add("event-1", "token", {"content": "1"})
        await buffer.clear()

        events = await buffer.get_events_after("")
        assert len(events) == 0


class TestSSEStream:
    """Tests for SSEStream class."""

    def test_init_default_values(self) -> None:
        """Test initialization with default values."""
        stream = SSEStream()
        assert stream.retry_ms == 3000
        assert stream.heartbeat_interval == 15.0
        assert stream._event_buffer._max_size == 100

    def test_init_custom_values(self) -> None:
        """Test initialization with custom values."""
        stream = SSEStream(retry_ms=5000, heartbeat_interval=30.0, buffer_size=50)
        assert stream.retry_ms == 5000
        assert stream.heartbeat_interval == 30.0
        assert stream._event_buffer._max_size == 50

    @patch("server.app.api.sse.get_settings")
    def test_from_settings(self, mock_get_settings: MagicMock) -> None:
        """Test creating SSEStream from settings."""
        mock_settings = MagicMock(spec=Settings)
        mock_settings.sse_retry_interval_ms = 5000
        mock_settings.sse_heartbeat_interval_seconds = 45.0
        mock_settings.sse_buffer_size = 200
        mock_get_settings.return_value = mock_settings

        stream = SSEStream.from_settings()

        assert stream.retry_ms == 5000
        assert stream.heartbeat_interval == 45.0
        assert stream._event_buffer._max_size == 200

    @pytest.mark.asyncio
    async def test_generate_event_id(self) -> None:
        """Test event ID generation."""
        stream = SSEStream()

        id1 = await stream._generate_event_id()
        id2 = await stream._generate_event_id()

        assert id1 != id2
        assert "-" in id1  # Format: counter-uuid_prefix
        parts1 = id1.split("-")
        parts2 = id2.split("-")
        assert len(parts1) == 2
        assert len(parts2) == 2
        assert parts1[0] == "1"  # First counter value
        assert parts2[0] == "2"  # Second counter value
        assert parts1[1] != parts2[1]  # Different UUIDs

    def test_format_event(self) -> None:
        """Test static format_event method."""
        formatted = SSEStream.format_event("token", {"content": "hello"})
        assert "event: token" in formatted
        assert '"content": "hello"' in formatted

    def test_format_event_with_id(self) -> None:
        """Test formatting event with ID."""
        formatted = SSEStream.format_event("token", {"content": "hello"}, event_id="123")
        assert "id: 123" in formatted

    def test_format_event_with_retry(self) -> None:
        """Test formatting event with retry."""
        formatted = SSEStream.format_event("token", {"content": "hello"}, retry_ms=5000)
        assert "retry: 5000" in formatted

    def test_format_keepalive(self) -> None:
        """Test keepalive formatting."""
        keepalive = SSEStream.format_keepalive()
        assert keepalive.startswith(":")
        assert keepalive.endswith("\n\n")

    def test_format_keepalive_with_message(self) -> None:
        """Test keepalive with custom message."""
        keepalive = SSEStream.format_keepalive("ping")
        assert keepalive == ":ping\n\n"

    @pytest.mark.asyncio
    async def test_event_generator_sends_retry_first(self) -> None:
        """Test that retry directive is sent first."""
        stream = SSEStream(retry_ms=5000)

        async def mock_stream():
            yield {"event": "token", "data": {"content": "hello"}}

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)

        events = []
        async for event in stream.event_generator(mock_stream(), mock_request):
            events.append(event)

        assert len(events) >= 2
        assert events[0] == "retry: 5000\n\n"

    @pytest.mark.asyncio
    async def test_event_generator_includes_event_id(self) -> None:
        """Test that events include IDs."""
        stream = SSEStream()

        async def mock_stream():
            yield {"event": "token", "data": {"content": "hello"}}

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)

        events = []
        async for event in stream.event_generator(mock_stream(), mock_request):
            events.append(event)

        # Find the data event (skip retry directive)
        data_events = [e for e in events if "event: token" in e]
        assert len(data_events) == 1
        assert "id:" in data_events[0]

    @pytest.mark.asyncio
    async def test_event_generator_buffers_events(self) -> None:
        """Test that events are buffered for replay."""
        stream = SSEStream()

        async def mock_stream():
            yield {"event": "token", "data": {"content": "hello"}}

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)

        async for _ in stream.event_generator(mock_stream(), mock_request):
            pass

        # Check that event was buffered
        buffered = await stream._event_buffer.get_events_after("")
        assert len(buffered) == 1
        assert buffered[0].event_type == "token"

    @pytest.mark.asyncio
    async def test_event_generator_resumption(self) -> None:
        """Test stream resumption with Last-Event-ID."""
        stream = SSEStream()

        # Pre-populate buffer with events
        await stream._event_buffer.add("event-1", "token", {"content": "1"})
        await stream._event_buffer.add("event-2", "token", {"content": "2"})
        await stream._event_buffer.add("event-3", "token", {"content": "3"})

        async def mock_stream():
            # No new events
            return
            yield  # Make it a generator

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)

        events = []
        async for event in stream.event_generator(
            mock_stream(), mock_request, last_event_id="event-1"
        ):
            events.append(event)

        # Should include retry, buffered events after event-1, and reconnected event
        assert any("retry:" in e for e in events)
        assert any('"content": "2"' in e for e in events)
        assert any('"content": "3"' in e for e in events)
        assert any("reconnected" in e for e in events)

    @pytest.mark.asyncio
    async def test_event_generator_sends_keepalive(self) -> None:
        """Test that keepalive is sent during idle periods."""
        stream = SSEStream(heartbeat_interval=0.01)  # Very short interval

        async def slow_stream():
            await asyncio.sleep(0.05)  # Longer than heartbeat
            yield {"event": "token", "data": {"content": "hello"}}

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = AsyncMock(return_value=False)

        events = []
        async for event in stream.event_generator(slow_stream(), mock_request):
            events.append(event)
            if len(events) > 5:  # Prevent infinite loop
                break

        # Should have at least one keepalive (starts with ':')
        keepalives = [e for e in events if e.startswith(":")]
        assert len(keepalives) >= 1

    @pytest.mark.asyncio
    async def test_event_generator_handles_disconnect(self) -> None:
        """Test that generator stops on client disconnect."""
        stream = SSEStream()

        async def mock_stream():
            for i in range(100):
                yield {"event": "token", "data": {"content": str(i)}}

        mock_request = MagicMock(spec=Request)
        # Disconnect after first check
        mock_request.is_disconnected = AsyncMock(side_effect=[False, True, True])

        events = []
        async for event in stream.event_generator(mock_stream(), mock_request):
            events.append(event)
            if len(events) > 10:
                break

        # Should have stopped early due to disconnect
        assert len(events) < 100


class TestEventBuilder:
    """Tests for EventBuilder utility class."""

    def test_token_event(self) -> None:
        """Test creating token event."""
        event = EventBuilder.token("hello world")
        assert event["event"] == "token"
        assert event["data"]["content"] == "hello world"

    def test_tool_call_event(self) -> None:
        """Test creating tool call event."""
        event = EventBuilder.tool_call(
            name="execute", args={"command": "ls"}, tool_call_id="call-1"
        )
        assert event["event"] == "tool_call"
        assert event["data"]["name"] == "execute"
        assert event["data"]["args"]["command"] == "ls"
        assert event["data"]["id"] == "call-1"

    def test_tool_result_event(self) -> None:
        """Test creating tool result event."""
        event = EventBuilder.tool_result(tool_call_id="call-1", output="result", exit_code=0)
        assert event["event"] == "tool_result"
        assert event["data"]["tool_call_id"] == "call-1"
        assert event["data"]["output"] == "result"
        assert event["data"]["exit_code"] == 0

    def test_error_event(self) -> None:
        """Test creating error event."""
        event = EventBuilder.error("Something went wrong", code="ERR_001")
        assert event["event"] == "error"
        assert event["data"]["message"] == "Something went wrong"
        assert event["data"]["code"] == "ERR_001"

    def test_error_event_without_code(self) -> None:
        """Test creating error event without code."""
        event = EventBuilder.error("Something went wrong")
        assert event["event"] == "error"
        assert event["data"]["message"] == "Something went wrong"
        assert "code" not in event["data"]

    def test_done_event(self) -> None:
        """Test creating done event."""
        event = EventBuilder.done()
        assert event["event"] == "done"
        assert event["data"] == {}

    def test_usage_event(self) -> None:
        """Test creating usage event."""
        event = EventBuilder.usage(
            input_tokens=100,
            output_tokens=50,
            estimated_cost=0.002,
            provider="openai",
            model="gpt-4",
        )
        assert event["event"] == "usage"
        assert event["data"]["input_tokens"] == 100
        assert event["data"]["output_tokens"] == 50
        assert event["data"]["estimated_cost"] == 0.002
        assert event["data"]["provider"] == "openai"
        assert event["data"]["model"] == "gpt-4"

    def test_planning_event(self) -> None:
        """Test creating planning event."""
        todos = [{"task": "step 1"}, {"task": "step 2"}]
        event = EventBuilder.planning(todos)
        assert event["event"] == "planning"
        assert event["data"]["todos"] == todos

    def test_step_complete_event(self) -> None:
        """Test creating step complete event."""
        event = EventBuilder.step_complete(
            step_number=2, total_steps=5, description="Did something"
        )
        assert event["event"] == "step_complete"
        assert event["data"]["step_number"] == 2
        assert event["data"]["total_steps"] == 5
        assert event["data"]["description"] == "Did something"

    def test_status_event(self) -> None:
        """Test creating status event."""
        event = EventBuilder.status("processing")
        assert event["event"] == "status"
        assert event["data"]["status"] == "processing"

    def test_reconnected_event(self) -> None:
        """Test creating reconnected event."""
        event = EventBuilder.reconnected("event-123")
        assert event["event"] == "reconnected"
        assert event["data"]["last_event_id"] == "event-123"
        assert event["data"]["resumed"] is True


class TestGetLastEventId:
    """Tests for get_last_event_id helper function."""

    def test_get_last_event_id_present(self) -> None:
        """Test extracting Last-Event-ID header."""
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"Last-Event-ID": "event-123"}

        result = get_last_event_id(mock_request)
        assert result == "event-123"

    def test_get_last_event_id_missing(self) -> None:
        """Test when Last-Event-ID header is missing."""
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {}

        result = get_last_event_id(mock_request)
        assert result is None

    def test_get_last_event_id_empty(self) -> None:
        """Test when Last-Event-ID header is empty."""
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"Last-Event-ID": ""}

        result = get_last_event_id(mock_request)
        assert result == ""
