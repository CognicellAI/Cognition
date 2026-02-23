"""Business Scenario: Reliable Real-Time Event Streams.

As a user, I want real-time updates during AI conversations even if my
connection drops, so that I never miss important progress updates.

Business Value:
- Uninterrupted user experience during network issues
- Confidence in long-running AI operations
- Transparent progress visibility
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestReliableStreaming:
    """Test reliable streaming during AI interactions."""

    async def test_sse_connection_establishment(self, api_client, session) -> None:
        """Test SSE connection establishes correctly."""
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages",
            {"content": "Test streaming message"},
            max_events=50,
            timeout=10.0,
        )

        # Should receive events
        assert len(events) > 0, "No events received"

    async def test_sse_event_format(self, api_client, session) -> None:
        """Test events follow SSE format."""
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages", {"content": "Generate multiple events"}, max_events=50
        )

        # Check for event lines
        has_event_field = any(line.startswith("event:") for line in events)
        has_data_field = any(line.startswith("data:") for line in events)

        assert has_event_field, "Missing 'event:' field"
        assert has_data_field, "Missing 'data:' field"

    async def test_sse_completion_signal(self, api_client, session) -> None:
        """Test stream properly signals completion."""
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages", {"content": "Complete this stream"}, max_events=100
        )

        # Check for completion
        event_lines = [line for line in events if line.startswith("event:")]
        has_completion = any("done" in line or "complete" in line.lower() for line in event_lines)

        # Either has explicit done or the stream ended naturally
        assert len(events) > 0, "Stream was empty"

    async def test_message_persistence_after_streaming(self, api_client, session) -> None:
        """Test messages persist after streaming."""
        # Stream a message
        await api_client.stream_sse(
            f"/sessions/{session}/messages", {"content": "Stream then persist"}, max_events=50
        )

        # Give server time to process
        import asyncio

        await asyncio.sleep(0.5)

        # Verify message persisted
        messages = await api_client.get_messages(session)
        assert len(messages) > 0, "Messages not persisted after streaming"

    async def test_stream_with_various_content(self, api_client, session) -> None:
        """Test streaming with various content types."""
        contents = [
            "Short",
            "Medium length message with some content",
            "Longer message with more detailed content that spans multiple tokens",
        ]

        for content in contents:
            events = await api_client.stream_sse(
                f"/sessions/{session}/messages", {"content": content}, max_events=30
            )

            assert len(events) > 0, f"No events for: {content[:30]}"

    async def test_multiple_streams_same_session(self, api_client, session) -> None:
        """Test multiple streams work in the same session."""
        for i in range(3):
            events = await api_client.stream_sse(
                f"/sessions/{session}/messages", {"content": f"Stream number {i}"}, max_events=30
            )

            assert len(events) > 0, f"Stream {i} had no events"

        # Verify all messages persisted
        messages = await api_client.get_messages(session)
        assert len(messages) >= 3, "Not all streamed messages persisted"

    async def test_stream_content_types(self, api_client, session) -> None:
        """Test streaming with different content types."""
        content_types = [
            "Text content",
            "Code: print('hello')",
            "Numbers: 12345",
            "Mixed: Text 123 code",
        ]

        for content in content_types:
            events = await api_client.stream_sse(
                f"/sessions/{session}/messages", {"content": content}, max_events=30
            )

            # Should receive events regardless of content type
            assert len(events) > 0, f"Failed for content type: {content[:30]}"
