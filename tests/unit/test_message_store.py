"""Tests for message persistence (P0-1).

Tests for the SQLite-backed message storage replacing in-memory dict.
"""

import pytest
import tempfile
from datetime import datetime
from pathlib import Path

from server.app.message_store import SqliteMessageStore, get_message_store
from server.app.models import Message


class TestSqliteMessageStore:
    """Test suite for SqliteMessageStore."""

    @pytest.fixture
    def temp_store(self):
        """Create a temporary message store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield SqliteMessageStore(tmpdir)

    @pytest.mark.asyncio
    async def test_create_message(self, temp_store):
        """Test creating a message."""
        message = await temp_store.create_message(
            message_id="msg-1",
            session_id="session-1",
            role="user",
            content="Hello, world!",
            parent_id=None,
        )

        assert message.id == "msg-1"
        assert message.session_id == "session-1"
        assert message.role == "user"
        assert message.content == "Hello, world!"
        assert message.parent_id is None
        assert isinstance(message.created_at, datetime)

    @pytest.mark.asyncio
    async def test_get_message(self, temp_store):
        """Test retrieving a message by ID."""
        # Create a message
        created = await temp_store.create_message(
            message_id="msg-1",
            session_id="session-1",
            role="user",
            content="Test content",
        )

        # Retrieve it
        retrieved = await temp_store.get_message("msg-1")

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.content == created.content
        assert retrieved.role == created.role

    @pytest.mark.asyncio
    async def test_get_message_not_found(self, temp_store):
        """Test retrieving a non-existent message."""
        retrieved = await temp_store.get_message("non-existent")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_messages_by_session(self, temp_store):
        """Test retrieving messages for a session."""
        # Create multiple messages
        await temp_store.create_message("msg-1", "session-1", "user", "First")
        await temp_store.create_message("msg-2", "session-1", "assistant", "Second")
        await temp_store.create_message("msg-3", "session-2", "user", "Different session")

        # Get messages for session-1
        messages, total = await temp_store.get_messages_by_session("session-1")

        assert total == 2
        assert len(messages) == 2
        assert messages[0].content == "First"
        assert messages[1].content == "Second"

    @pytest.mark.asyncio
    async def test_get_messages_pagination(self, temp_store):
        """Test message pagination."""
        # Create 5 messages
        for i in range(5):
            await temp_store.create_message(f"msg-{i}", "session-1", "user", f"Message {i}")

        # Get first 2
        messages, total = await temp_store.get_messages_by_session("session-1", limit=2, offset=0)
        assert total == 5
        assert len(messages) == 2

        # Get next 2
        messages, total = await temp_store.get_messages_by_session("session-1", limit=2, offset=2)
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_delete_messages_for_session(self, temp_store):
        """Test deleting all messages for a session."""
        # Create messages
        await temp_store.create_message("msg-1", "session-1", "user", "Content")
        await temp_store.create_message("msg-2", "session-2", "user", "Other")

        # Delete session-1 messages
        deleted = await temp_store.delete_messages_for_session("session-1")
        assert deleted == 1

        # Verify session-1 messages are gone
        messages, total = await temp_store.get_messages_by_session("session-1")
        assert total == 0

        # Verify session-2 messages remain
        messages, total = await temp_store.get_messages_by_session("session-2")
        assert total == 1


class TestMessagePersistenceAcrossRestarts:
    """Test that messages persist across server restarts."""

    @pytest.mark.asyncio
    async def test_messages_survive_store_recreation(self):
        """Test messages survive when store is recreated (simulating restart)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create store and message
            store1 = SqliteMessageStore(tmpdir)
            await store1.create_message("msg-1", "session-1", "user", "Persistent")

            # Create new store instance (simulating restart)
            store2 = SqliteMessageStore(tmpdir)

            # Message should still exist
            retrieved = await store2.get_message("msg-1")
            assert retrieved is not None
            assert retrieved.content == "Persistent"


class TestMessageStoreCache:
    """Test the global message store cache."""

    def test_get_message_store_caching(self):
        """Test that get_message_store returns cached instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = get_message_store(tmpdir)
            store2 = get_message_store(tmpdir)

            # Should be the same instance
            assert store1 is store2
