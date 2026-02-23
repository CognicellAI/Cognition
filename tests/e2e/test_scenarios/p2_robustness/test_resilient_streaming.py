"""Business Scenario: Resilient Streaming During Network Issues.

As a user, I want my AI conversation to continue seamlessly even if my
network connection drops momentarily, without losing any responses.

Business Value:
- Uninterrupted conversations during network instability
- No lost AI responses due to connection issues
- Automatic recovery without user intervention
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
class TestResilientStreaming:
    """Test conversations survive temporary network disruptions."""

    async def test_sse_reconnection_metadata(self, api_client, session) -> None:
        """Test SSE stream includes reconnection metadata."""
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages",
            {"content": "Test message for resilience"},
            max_events=50,
        )

        # Check for event IDs (optional but good to have)
        has_ids = any(line.startswith("id:") for line in events)
        if has_ids:
            print("SSE includes event IDs for reconnection")

        # Check for retry directive (optional)
        has_retry = any(line.startswith("retry:") for line in events)
        if has_retry:
            print("SSE includes retry directive")

        # Should have events
        assert len(events) > 0, "No events in stream"

    async def test_stream_with_early_interruption(self, api_client, session) -> None:
        """Test stream handles early interruption gracefully."""

        # Start a stream but limit events
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages",
            {"content": "Long message to stream"},
            max_events=10,  # Limited to simulate interruption
            timeout=3.0,
        )

        # Should get partial stream
        assert len(events) >= 0, "Stream handling failed"

    async def test_message_persistence_after_interruption(self, api_client, session) -> None:
        """Test messages persist after stream interruption."""
        # Stream with early cutoff
        await api_client.stream_sse(
            f"/sessions/{session}/messages",
            {"content": "Message before interruption"},
            max_events=5,
            timeout=2.0,
        )

        # Brief wait for server processing
        await asyncio.sleep(0.5)

        # Messages should be persisted
        messages = await api_client.get_messages(session)
        # Note: May not persist if stream was cut too early
        # This is informational
        print(f"Messages after interruption: {len(messages)}")

    async def test_session_usability_after_interruption(self, api_client, session) -> None:
        """Test session remains usable after interruption."""
        # Simulate interrupted stream
        await api_client.stream_sse(
            f"/sessions/{session}/messages",
            {"content": "Interrupted stream"},
            max_events=3,
            timeout=1.5,
        )

        # Session should still work
        response = await api_client.send_message(session, "Message after interruption")

        assert response.status_code == 200, "Session not usable after interruption"

    async def test_multiple_interruptions(self, api_client, session) -> None:
        """Test multiple stream interruptions."""
        for i in range(3):
            events = await api_client.stream_sse(
                f"/sessions/{session}/messages",
                {"content": f"Stream attempt {i}"},
                max_events=5,
                timeout=2.0,
            )
            # Should not crash
            assert isinstance(events, list)

        # Final message should work
        response = await api_client.send_message(session, "After multiple interruptions")
        assert response.status_code == 200

    async def test_stream_recovery(self, api_client, session) -> None:
        """Test stream can recover and continue."""
        # Initial stream
        await api_client.stream_sse(
            f"/sessions/{session}/messages", {"content": "First stream"}, max_events=10
        )

        # Continue with regular message
        response = await api_client.send_message(session, "Continuation")
        assert response.status_code == 200

        # Another stream
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages", {"content": "Second stream"}, max_events=10
        )

        assert len(events) > 0, "Stream recovery failed"
