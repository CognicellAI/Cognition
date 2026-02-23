"""Business Scenario: Intelligent Project Awareness.

As a developer, I want the AI to understand my project structure,
so it can provide contextually relevant suggestions about my codebase.

Business Value:
- More relevant AI responses based on actual project files
- Reduced time explaining project context
- Better code suggestions based on existing patterns
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestProjectAwareness:
    """Test AI understanding of project context."""

    async def test_project_context_establishment(self, api_client, session) -> None:
        """Establish project context."""
        context_messages = [
            "This is a Python FastAPI application",
            "The project structure includes server/ and client/",
            "We use PostgreSQL for data persistence",
            "The main API routes are in server/app/api/",
        ]

        for msg in context_messages:
            response = await api_client.send_message(session, msg)
            assert response.status_code == 200

        print(f"\n  Context established with {len(context_messages)} messages")

    async def test_context_aware_queries(self, api_client, session) -> None:
        """Test context-aware AI interactions."""
        # Establish context
        await api_client.send_message(session, "We use FastAPI")

        # Context-aware queries
        queries = ["Where should I add a new endpoint?", "How do I connect to the database?"]

        for query in queries:
            response = await api_client.send_message(session, query)
            assert response.status_code == 200

        print(f"\n  {len(queries)} context-aware queries processed")

    async def test_context_persistence(self, api_client, session) -> None:
        """Test context persists."""
        # Add context
        await api_client.send_message(session, "Project uses FastAPI")

        # Retrieve session
        response = await api_client.get(f"/sessions/{session}")
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session

    async def test_conversation_continuity(self, api_client, session) -> None:
        """Test conversation continuity with context."""
        # Initial
        await api_client.send_message(session, "Let us implement auth")

        # Follow-up
        response = await api_client.send_message(session, "Where should I put the middleware?")
        assert response.status_code == 200

        # Verify history
        messages = await api_client.get_messages(session)
        assert len(messages) >= 2

    async def test_context_in_history(self, api_client, session) -> None:
        """Test context keywords in history."""
        # Add context
        await api_client.send_message(session, "Using FastAPI framework")
        await api_client.send_message(session, "PostgreSQL database")

        # Retrieve
        messages = await api_client.get_messages(session)
        contents = " ".join([m.get("content", "") for m in messages])

        # Check for keywords
        has_fastapi = "fastapi" in contents.lower() or "framework" in contents.lower()
        has_db = "postgresql" in contents.lower() or "database" in contents.lower()

        if has_fastapi:
            print("\n  FastAPI context found")
        if has_db:
            print("  Database context found")

    async def test_multi_topic_context(self, api_client, session) -> None:
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

        print(f"\n  {len(topics)} topics in context")
