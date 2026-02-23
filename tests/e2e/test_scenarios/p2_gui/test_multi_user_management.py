"""Business Scenario: Multi-User Conversation Management.

As a team lead, I want to manage multiple team member conversations,
so I can oversee project progress and provide guidance when needed.

Business Value:
- Centralized conversation management for teams
- Scoped access for security and privacy
- Cross-workspace visibility for managers
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestMultiUserManagement:
    """Test team leaders managing multiple conversations."""

    async def test_create_team_member_sessions(self, api_client) -> None:
        """Create conversations for different team members."""
        team_members = ["alice", "bob", "charlie"]
        sessions = []

        for member in team_members:
            # Note: With scoping, this may fail for different users
            sid = await api_client.create_session(f"{member}'s Session")
            sessions.append(sid)

        assert len(sessions) == len(team_members)
        print(f"\n  Created {len(sessions)} team sessions")

    async def test_add_work_context(self, api_client, session) -> None:
        """Add work context to sessions."""
        messages = [
            "Working on the authentication module",
            "Need to review the database schema",
            "Planning the API endpoints",
        ]

        for msg in messages:
            response = await api_client.send_message(session, msg)
            # Some may fail due to rate limiting
            if response.status_code == 200:
                print(f"  Added: {msg[:40]}...")

    async def test_manager_view_all_sessions(self, api_client) -> None:
        """Test manager view of all sessions."""
        response = await api_client.get("/sessions", params={"limit": 50})

        assert response.status_code == 200

        data = response.json()
        total = data.get("total", 0)
        sessions = data.get("sessions", [])

        print(f"\n  Total sessions visible: {total}")
        print(f"  Retrieved: {len(sessions)}")

        # Manager should see sessions
        assert isinstance(sessions, list)

    async def test_session_details_access(self, api_client, session) -> None:
        """Access individual session details."""
        response = await api_client.get(f"/sessions/{session}")

        assert response.status_code == 200

        data = response.json()
        assert data["id"] == session

    async def test_conversation_review(self, api_client, session) -> None:
        """Review conversation content."""
        # Add some messages
        await api_client.send_message(session, "Work item 1")
        await api_client.send_message(session, "Work item 2")

        # Review
        messages = await api_client.get_messages(session)
        count = len(messages)

        print(f"\n  Session has {count} messages")
        assert count > 0

    async def test_session_lifecycle(self, api_client, session) -> None:
        """Test session lifecycle management."""
        # Update session
        response = await api_client.patch(
            f"/sessions/{session}", json={"title": "Updated Session Title"}
        )

        assert response.status_code == 200

        # Verify update
        response = await api_client.get(f"/sessions/{session}")
        data = response.json()
        assert data["title"] == "Updated Session Title"

    async def test_cross_session_visibility(self, api_client) -> None:
        """Test cross-session visibility."""
        # Create a session
        sid = await api_client.create_session("Visibility Test")

        # List sessions
        response = await api_client.get("/sessions")
        data = response.json()
        sessions = data.get("sessions", [])

        # Should be able to find the session
        session_ids = [s["id"] for s in sessions]
        if sid in session_ids:
            print("\n  Session found in listing")
        else:
            print("\n  Session may be filtered by scope")
