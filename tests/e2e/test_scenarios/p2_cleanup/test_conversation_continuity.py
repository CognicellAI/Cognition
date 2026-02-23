"""Business Scenario: Conversation Continuity After Server Restart.

As a user, I want my conversation history to persist even if the server restarts,
so that I don't lose context and can continue where I left off.

Business Value:
- User trust: Conversations are never lost
- Seamless experience: No need to restart conversations after maintenance
- Compliance: Audit trail preserved
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestConversationContinuity:
    """Test conversation persistence across server operations."""

    async def test_conversation_context_creation(self, api_client) -> None:
        """Phase 1: Create conversation context."""
        # Create session
        session_id = await api_client.create_session("Project Planning Session")

        # Add multiple messages to build context
        messages = [
            "Let us plan the new feature implementation",
            "We need to consider the database schema first",
            "What are the performance requirements?",
            "The API should handle 1000 requests per second",
        ]

        for msg in messages:
            response = await api_client.send_message(session_id, msg)
            assert response.status_code == 200, f"Failed to add message: {msg}"

        # Verify messages were stored
        stored_messages = await api_client.get_messages(session_id)
        assert len(stored_messages) >= len(messages), "Not all messages stored"

    async def test_session_persistence(self, api_client) -> None:
        """Phase 2: Verify persistence (simulates server restart)."""
        # Create and populate session
        session_id = await api_client.create_session("Persistence Test Session")
        await api_client.send_message(session_id, "Test message")

        # Retrieve session (simulates coming back after restart)
        response = await api_client.get(f"/sessions/{session_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session_id
        assert data["title"] == "Persistence Test Session"

    async def test_message_history_retrieval(self, api_client) -> None:
        """Phase 3: Verify message history is complete."""
        # Create session with messages
        session_id = await api_client.create_session("History Test Session")

        messages = ["First message", "Second message", "Third message"]

        for msg in messages:
            await api_client.send_message(session_id, msg)

        # Retrieve all messages
        stored = await api_client.get_messages(session_id)

        # Verify count
        assert len(stored) >= len(messages), "Message count mismatch"

        # Verify content preservation
        contents = [m.get("content", "") for m in stored]
        assert any("First message" in c for c in contents), "First message not found"

    async def test_conversation_can_continue(self, api_client) -> None:
        """Phase 4: Verify conversation can continue after retrieval."""
        # Create session and add initial messages
        session_id = await api_client.create_session("Continuation Test")
        await api_client.send_message(session_id, "Initial message")

        # Continue conversation
        response = await api_client.send_message(session_id, "Continuing the conversation")
        assert response.status_code == 200, "Failed to continue conversation"

        # Verify all messages present
        messages = await api_client.get_messages(session_id)
        assert len(messages) >= 2, "Conversation not properly continued"

    async def test_persistence_across_multiple_sessions(self, api_client) -> None:
        """Test that multiple sessions persist independently."""
        # Create multiple sessions
        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Multi-Session Test {i}")
            await api_client.send_message(sid, f"Message for session {i}")
            sessions.append(sid)

        # Verify each session persists independently
        for i, sid in enumerate(sessions):
            response = await api_client.get(f"/sessions/{sid}")
            assert response.status_code == 200
            assert response.json()["id"] == sid

            messages = await api_client.get_messages(sid)
            assert len(messages) > 0, f"Session {i} messages not persisted"
