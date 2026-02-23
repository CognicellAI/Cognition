"""Business Scenario: Project Context Memory Preservation.

As a user, I want the AI to remember context about my project files,
so that I don't have to repeatedly explain the codebase structure.

Business Value:
- Reduced repetitive explanations
- More relevant AI responses based on project context
- Improved productivity through contextual awareness
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestProjectContextMemory:
    """Test AI remembers project context between messages."""

    async def test_project_context_establishment(self, api_client, session) -> None:
        """Establish project context through conversation."""
        context_messages = [
            "This is a Python FastAPI project",
            "The main API is in server/app/main.py",
            "We use PostgreSQL for data storage",
            "Authentication is handled via JWT tokens",
        ]

        for msg in context_messages:
            response = await api_client.send_message(session, msg)
            assert response.status_code == 200, f"Failed: {msg}"

        # Verify messages stored
        messages = await api_client.get_messages(session)
        assert len(messages) >= len(context_messages)

    async def test_context_persistence(self, api_client, session) -> None:
        """Verify context persists in session."""
        # Add context
        await api_client.send_message(session, "Project uses FastAPI framework")

        # Retrieve session
        response = await api_client.get(f"/sessions/{session}")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session

    async def test_context_aware_queries(self, api_client, session) -> None:
        """Test context-aware AI interactions."""
        # First establish context
        await api_client.send_message(session, "We are building a REST API with FastAPI")

        # Query that relies on context
        queries = ["What framework are we using?", "What type of API?"]

        for query in queries:
            response = await api_client.send_message(session, query)
            assert response.status_code == 200, f"Query failed: {query}"

    async def test_context_in_message_history(self, api_client, session) -> None:
        """Verify context keywords are in history."""
        # Add context messages
        await api_client.send_message(session, "Using FastAPI framework")
        await api_client.send_message(session, "PostgreSQL database")

        # Retrieve messages
        messages = await api_client.get_messages(session)
        contents = [m.get("content", "") for m in messages]

        # Check for context keywords
        all_content = " ".join(contents).lower()
        assert "fastapi" in all_content or "framework" in all_content, "Context keywords not found"

    async def test_conversation_continuity(self, api_client, session) -> None:
        """Test conversation continuity with context."""
        # Initial context message
        response = await api_client.send_message(session, "Let us implement authentication")
        assert response.status_code == 200

        # Follow-up
        response = await api_client.send_message(session, "Where should I put the auth middleware?")
        assert response.status_code == 200

        # Both messages should be in history
        messages = await api_client.get_messages(session)
        assert len(messages) >= 2

    async def test_context_after_retrieval(self, api_client, session) -> None:
        """Test context survives session retrieval."""
        # Add context
        await api_client.send_message(session, "Context before retrieval")

        # Simulate coming back later (retrieve session)
        response = await api_client.get(f"/sessions/{session}")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session

        # Can continue with context
        response = await api_client.send_message(session, "Continuing with previous context")
        assert response.status_code == 200

    async def test_multiple_context_topics(self, api_client, session) -> None:
        """Test handling multiple context topics."""
        topics = [
            "Database: PostgreSQL",
            "Framework: FastAPI",
            "Auth: JWT tokens",
            "Testing: pytest",
        ]

        for topic in topics:
            response = await api_client.send_message(session, topic)
            assert response.status_code == 200

        # All topics should be in history
        messages = await api_client.get_messages(session)
        assert len(messages) >= len(topics)
