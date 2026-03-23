"""Tests for message persistence (P0-1).

Tests for the StorageBackend message operations (unified storage layer).
"""

import tempfile
from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from server.app.models import SessionConfig
from server.app.storage.memory import MemoryStorageBackend
from server.app.storage.message_projection import project_checkpoint_messages
from server.app.storage.sqlite import SqliteStorageBackend


class TestStorageBackendMessages:
    """Test suite for StorageBackend message operations."""

    @pytest.fixture
    async def temp_storage(self):
        """Create a temporary storage backend."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SqliteStorageBackend(
                connection_string=f"{tmpdir}/test.db",
                workspace_path=tmpdir,
            )
            await storage.initialize()

            # Create a session for messages
            await storage.create_session(
                session_id="session-1",
                thread_id="thread-1",
                config=SessionConfig(),
            )

            yield storage
            await storage.close()

    @pytest.mark.asyncio
    async def test_create_message(self, temp_storage):
        """Test creating a message."""
        message = await temp_storage.create_message(
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
    async def test_get_message(self, temp_storage):
        """Test retrieving a message by ID."""
        # Create a message
        created = await temp_storage.create_message(
            message_id="msg-1",
            session_id="session-1",
            role="user",
            content="Test content",
        )

        # Retrieve it
        retrieved = await temp_storage.get_message("msg-1")

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.content == created.content
        assert retrieved.role == created.role

    @pytest.mark.asyncio
    async def test_get_message_not_found(self, temp_storage):
        """Test retrieving a non-existent message."""
        retrieved = await temp_storage.get_message("non-existent")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_messages_by_session(self, temp_storage):
        """Test retrieving messages for a session."""
        # Create multiple messages
        await temp_storage.create_message("msg-1", "session-1", "user", "First")
        await temp_storage.create_message("msg-2", "session-1", "assistant", "Second")

        # Create another session with a message
        await temp_storage.create_session(
            session_id="session-2",
            thread_id="thread-2",
            config=SessionConfig(),
        )
        await temp_storage.create_message("msg-3", "session-2", "user", "Different session")

        # Get messages for session-1
        messages, total = await temp_storage.get_messages_by_session("session-1")

        assert total == 2
        assert len(messages) == 2
        assert messages[0].content == "First"
        assert messages[1].content == "Second"

    @pytest.mark.asyncio
    async def test_get_messages_pagination(self, temp_storage):
        """Test message pagination."""
        # Create 5 messages
        for i in range(5):
            await temp_storage.create_message(f"msg-{i}", "session-1", "user", f"Message {i}")

        # Get first 2
        messages, total = await temp_storage.get_messages_by_session("session-1", limit=2, offset=0)
        assert total == 5
        assert len(messages) == 2

        # Get next 2
        messages, total = await temp_storage.get_messages_by_session("session-1", limit=2, offset=2)
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_delete_messages_for_session(self, temp_storage):
        """Test deleting all messages for a session."""
        # Create messages
        await temp_storage.create_message("msg-1", "session-1", "user", "Content")

        # Create another session
        await temp_storage.create_session(
            session_id="session-2",
            thread_id="thread-2",
            config=SessionConfig(),
        )
        await temp_storage.create_message("msg-2", "session-2", "user", "Other")

        # Delete session-1 messages
        deleted = await temp_storage.delete_messages_for_session("session-1")
        assert deleted == 1

        # Verify session-1 messages are gone
        messages, total = await temp_storage.get_messages_by_session("session-1")
        assert total == 0

        # Verify session-2 messages remain
        messages, total = await temp_storage.get_messages_by_session("session-2")
        assert total == 1


class TestMessagePersistenceAcrossRestarts:
    """Test that messages persist across server restarts."""

    @pytest.mark.asyncio
    async def test_messages_survive_store_recreation(self):
        """Test messages survive when store is recreated (simulating restart)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/test.db"

            # Create storage and session
            storage1 = SqliteStorageBackend(
                connection_string=db_path,
                workspace_path=tmpdir,
            )
            await storage1.initialize()
            await storage1.create_session(
                session_id="session-1",
                thread_id="thread-1",
                config=SessionConfig(),
            )
            await storage1.create_message("msg-1", "session-1", "user", "Persistent")
            await storage1.close()

            # Create new storage instance (simulating restart)
            storage2 = SqliteStorageBackend(
                connection_string=db_path,
                workspace_path=tmpdir,
            )
            await storage2.initialize()

            # Message should still exist
            retrieved = await storage2.get_message("msg-1")
            assert retrieved is not None
            assert retrieved.content == "Persistent"

            await storage2.close()


class TestMessageProjectionRebuild:
    @pytest.mark.asyncio
    async def test_projection_rebuild_restores_missing_assistant_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SqliteStorageBackend(
                connection_string=f"{tmpdir}/test.db",
                workspace_path=tmpdir,
            )
            await storage.initialize()
            await storage.create_session(
                session_id="session-1",
                thread_id="thread-1",
                config=SessionConfig(),
            )

            await storage.create_message("msg-user", "session-1", "user", "hello")
            await storage.create_message("msg-assistant", "session-1", "assistant", "world")
            deleted = await storage.delete_messages_for_session("session-1")
            assert deleted == 2

            rebuilt = await storage.rebuild_message_projection(
                session_id="session-1",
                thread_id="thread-1",
                checkpoint_messages=[
                    HumanMessage(content="hello"),
                    AIMessage(content="world"),
                    ToolMessage(content="done", tool_call_id="tool-1"),
                ],
            )

            assert rebuilt == 3
            messages = await storage.list_messages_for_session("session-1")
            assert [message.role for message in messages] == ["user", "assistant", "tool"]
            assert messages[1].content == "world"
            assert messages[2].tool_call_id == "tool-1"

            session = await storage.get_session("session-1")
            assert session is not None
            assert session.message_count == 3

            await storage.close()

    def test_project_checkpoint_messages_marks_projection_source(self):
        messages = project_checkpoint_messages(
            session_id="session-1",
            checkpoint_messages=[HumanMessage(content="hi"), AIMessage(content="hello")],
        )

        assert len(messages) == 2
        assert messages[0].metadata == {"projection_source": "checkpoint"}
        assert messages[1].parent_id == messages[0].id

    @pytest.mark.asyncio
    async def test_memory_backend_rebuild_message_projection(self):
        storage = MemoryStorageBackend(workspace_path="/tmp")
        await storage.initialize()
        await storage.create_session(
            session_id="session-1",
            thread_id="thread-1",
            config=SessionConfig(),
        )

        rebuilt = await storage.rebuild_message_projection(
            session_id="session-1",
            thread_id="thread-1",
            checkpoint_messages=[HumanMessage(content="hi"), AIMessage(content="hello")],
        )

        assert rebuilt == 2
        messages = await storage.list_messages_for_session("session-1")
        assert [message.role for message in messages] == ["user", "assistant"]
