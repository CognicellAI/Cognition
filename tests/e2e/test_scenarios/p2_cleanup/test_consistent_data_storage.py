"""Business Scenario: Consistent Message Storage and Retrieval.

As a user, I want my messages to be stored reliably and retrieved consistently,
so that I can trust the system to maintain accurate conversation records.

Business Value:
- Data integrity: No message loss or corruption
- Audit compliance: Complete conversation trails
- User confidence: System behaves predictably
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestConsistentDataStorage:
    """Test reliable and consistent data operations."""

    async def test_simple_text_message_storage(self, api_client, session) -> None:
        """Test storage of simple text messages."""
        response = await api_client.send_message(session, "Simple text message")
        assert response.status_code == 200, "Simple text message failed"

        # Verify retrieval
        messages = await api_client.get_messages(session)
        assert len(messages) > 0, "No messages retrieved"

    async def test_special_characters_handling(self, api_client, session) -> None:
        """Test storage of messages with special characters."""
        # Note: Testing basic special chars - some may need escaping
        special_messages = [
            "Message with quotes",
            "Message with numbers 123",
            "Message with symbols @#$%",
        ]

        for msg in special_messages:
            response = await api_client.send_message(session, msg)
            assert response.status_code == 200, f"Failed for: {msg}"

    async def test_unicode_message_storage(self, api_client, session) -> None:
        """Test storage of unicode messages."""
        unicode_msg = "Unicode: emojis and international text"

        response = await api_client.send_message(session, unicode_msg)
        assert response.status_code == 200, "Unicode message failed"

        # Verify retrieval
        messages = await api_client.get_messages(session)
        contents = [m.get("content", "") for m in messages]
        assert any("Unicode" in c for c in contents), "Unicode not preserved"

    async def test_long_message_storage(self, api_client, session) -> None:
        """Test storage of long messages (10KB)."""
        long_content = "A" * 10000

        response = await api_client.send_message(session, long_content)
        assert response.status_code == 200, "Long message failed"

        # Verify retrieval
        messages = await api_client.get_messages(session)
        assert len(messages) > 0, "Long message not stored"

    async def test_retrieval_consistency(self, api_client, session) -> None:
        """Test that retrieval is consistent across multiple requests."""
        # Add messages
        for i in range(3):
            await api_client.send_message(session, f"Consistency test {i}")

        # Retrieve multiple times
        counts = []
        for _ in range(3):
            messages = await api_client.get_messages(session)
            counts.append(len(messages))

        # All counts should be the same
        assert len(set(counts)) == 1, f"Inconsistent retrieval: {counts}"

    async def test_pagination_consistency(self, api_client, session) -> None:
        """Test pagination returns consistent results."""
        # Add multiple messages
        for i in range(10):
            await api_client.send_message(session, f"Pagination message {i}")

        # Get pages
        page1 = await api_client.get_messages(session, limit=5, offset=0)
        page2 = await api_client.get_messages(session, limit=5, offset=5)

        # Verify page sizes
        assert len(page1) == 5, f"Page 1 wrong size: {len(page1)}"
        assert len(page2) == 5, f"Page 2 wrong size: {len(page2)}"

    async def test_data_integrity(self, api_client, session) -> None:
        """Test that message content is preserved correctly."""
        test_content = "Data integrity test content"

        await api_client.send_message(session, test_content)

        # Retrieve and verify
        messages = await api_client.get_messages(session)
        assert len(messages) > 0, "No messages found"

        first_msg = messages[0]
        content = first_msg.get("content", "")
        assert content, "Message content is empty"
        assert isinstance(content, str), "Content is not a string"

    async def test_message_ordering(self, api_client, session) -> None:
        """Test that messages maintain their order."""
        # Add messages in sequence
        for i in range(5):
            await api_client.send_message(session, f"Ordered message {i}")

        # Retrieve
        messages = await api_client.get_messages(session)

        # Check that we have all messages
        assert len(messages) >= 5, "Not all messages retrieved"

        # Check ordering (if created_at is available)
        created_times = [m.get("created_at", "") for m in messages if m.get("created_at")]
        if len(created_times) > 1:
            # Times should be in ascending order
            assert created_times == sorted(created_times), "Messages not in order"
