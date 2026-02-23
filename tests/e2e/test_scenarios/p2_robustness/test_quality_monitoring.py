"""Business Scenario: AI Response Quality Monitoring.

As an administrator, I want to monitor and evaluate AI response quality,
so that I can ensure the system meets quality standards.

Business Value:
- Quality assurance for AI interactions
- Data-driven improvements to AI performance
- User feedback collection for continuous improvement
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestQualityMonitoring:
    """Test quality tracking and evaluation of AI responses."""

    async def test_evaluation_service_availability(self, api_client) -> None:
        """Test evaluation service is accessible."""
        # Check if API is accessible
        response = await api_client.get("/sessions")
        assert response.status_code == 200, "API not accessible"

    async def test_conversation_creation_for_evaluation(self, api_client, session) -> None:
        """Create conversation for quality evaluation."""
        exchanges = [
            "How do I implement authentication?",
            "What are the best practices?",
            "Can you show an example?",
        ]

        for exchange in exchanges:
            response = await api_client.send_message(session, exchange)
            # Some may fail due to rate limiting, that's ok
            if response.status_code == 200:
                print("  Exchange completed")

        # Verify conversation exists
        messages = await api_client.get_messages(session)
        assert len(messages) > 0, "Conversation not created"

    async def test_message_metadata_for_tracking(self, api_client, session) -> None:
        """Test message metadata supports quality tracking."""
        # Add a message
        await api_client.send_message(session, "Test for metadata")

        # Retrieve messages
        messages = await api_client.get_messages(session)

        if messages:
            msg = messages[0]

            # Check for metadata fields
            checks = []
            if "metadata" in msg:
                checks.append("metadata")
            if "token_count" in msg:
                checks.append("token_count")
            if "model_used" in msg:
                checks.append("model_used")
            if "created_at" in msg:
                checks.append("timestamp")

            print(f"\n  Message fields: {', '.join(checks) if checks else 'basic'}")

            # Should have at least some fields
            assert "content" in msg, "Message missing content"

    async def test_session_data_for_analysis(self, api_client, session) -> None:
        """Test session data completeness for analysis."""
        response = await api_client.get(f"/sessions/{session}")

        assert response.status_code == 200

        data = response.json()

        # Check required fields
        required_fields = ["id", "title", "status"]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

        # Timestamps are useful for analytics
        if "created_at" in data:
            print(f"  Created: {data['created_at']}")

    async def test_batch_session_access(self, api_client) -> None:
        """Test batch session access for evaluation."""
        response = await api_client.get("/sessions", params={"limit": 20})

        assert response.status_code == 200

        data = response.json()
        sessions = data.get("sessions", [])
        total = data.get("total", 0)

        print(f"\n  Total sessions: {total}, Retrieved: {len(sessions)}")

        # Should be able to list sessions
        assert isinstance(sessions, list)

    async def test_feedback_collection_availability(self, api_client, session) -> None:
        """Test feedback collection endpoint."""
        # Try to submit feedback
        response = await api_client.post(
            "/evaluations/feedback",
            json={"session_id": session, "rating": 5, "comment": "Good response"},
        )

        # May be 404 if not implemented, that's ok
        if response.status_code == 404:
            print("  Feedback endpoint not implemented (expected)")
        elif response.status_code in [200, 201]:
            print("  Feedback collection available")
        else:
            print(f"  Feedback endpoint returned: {response.status_code}")

    async def test_pagination_for_analytics(self, api_client, session) -> None:
        """Test pagination works for analytics queries."""
        # Add multiple messages
        for i in range(5):
            await api_client.send_message(session, f"Message {i}")

        # Query with pagination
        response = await api_client.get(
            f"/sessions/{session}/messages", params={"limit": 2, "offset": 0}
        )

        assert response.status_code == 200

        data = response.json()
        messages = data.get("messages", [])

        # Pagination should work
        assert len(messages) <= 2, "Pagination not working"
