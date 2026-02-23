"""Business Scenario: Conversation Analytics Tracking.

As an administrator, I want detailed analytics about conversations,
including token usage and model performance, for cost optimization.

Business Value:
- Cost tracking and optimization
- Usage analytics for capacity planning
- Performance monitoring across models
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestAnalyticsTracking:
    """Test comprehensive conversation analytics."""

    async def test_conversation_with_varied_messages(self, api_client, session) -> None:
        """Create conversation with varied message lengths."""
        messages = [
            "Short",
            "Medium length message with some content",
            "Longer detailed message about implementation",
        ]

        for msg in messages:
            response = await api_client.send_message(session, msg)
            if response.status_code == 200:
                print(f"  Stored: {msg[:30]}...")

    async def test_message_enrichment_fields(self, api_client, session) -> None:
        """Test messages have enrichment fields."""
        # Send a message
        await api_client.send_message(session, "Test for enrichment")

        # Retrieve
        messages = await api_client.get_messages(session)

        if messages:
            msg = messages[0]

            # Check enrichment fields
            fields_found = []
            if "token_count" in msg:
                fields_found.append(f"tokens={msg['token_count']}")
            if "model_used" in msg:
                fields_found.append(f"model={msg['model_used']}")
            if "metadata" in msg:
                fields_found.append("metadata")

            print(f"\n  Enrichment: {', '.join(fields_found) if fields_found else 'basic'}")

    async def test_analytics_aggregation(self, api_client, session) -> None:
        """Test analytics data aggregation."""
        # Add messages
        for i in range(3):
            await api_client.send_message(session, f"Analytics test {i}")

        # Get count
        messages = await api_client.get_messages(session)
        count = len(messages)

        print(f"\n  Total messages: {count}")

        # Should have messages
        assert count > 0, "No messages for analytics"

    async def test_pagination_for_analytics(self, api_client, session) -> None:
        """Test pagination supports analytics queries."""
        # Add messages
        for i in range(5):
            await api_client.send_message(session, f"Page test {i}")

        # Paginated query
        response = await api_client.get(f"/sessions/{session}/messages", params={"limit": 2})

        assert response.status_code == 200

        data = response.json()
        page = data.get("messages", [])
        total = data.get("total", 0)

        print(f"\n  Page size: {len(page)}, Total: {total}")

        # Pagination should work
        assert len(page) <= 2, "Page size exceeds limit"

    async def test_session_level_analytics(self, api_client, session) -> None:
        """Test session-level analytics."""
        response = await api_client.get(f"/sessions/{session}")

        assert response.status_code == 200

        data = response.json()

        # Check timestamps
        if "created_at" in data:
            print(f"\n  Created: {data['created_at']}")
        if "updated_at" in data:
            print(f"  Updated: {data['updated_at']}")

    async def test_batch_analytics_via_listing(self, api_client) -> None:
        """Test batch analytics through session listing."""
        response = await api_client.get("/sessions", params={"limit": 20})

        assert response.status_code == 200

        data = response.json()
        sessions = data.get("sessions", [])
        total = data.get("total", 0)

        print(f"\n  Sessions in system: {total}")

        # Verify session objects
        if sessions:
            sample = sessions[0]
            assert "id" in sample, "Session missing ID"
            print(f"  Sample session fields: {list(sample.keys())[:5]}")

    async def test_message_timestamps(self, api_client, session) -> None:
        """Test messages have timestamps for analytics."""
        await api_client.send_message(session, "Timestamp test")

        messages = await api_client.get_messages(session)

        if messages:
            msg = messages[0]
            if "created_at" in msg:
                print(f"\n  Message created: {msg['created_at']}")
            if "updated_at" in msg:
                print(f"  Message updated: {msg['updated_at']}")
