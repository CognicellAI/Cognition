"""Unit tests for session scoping harness.

Tests that sessions are properly scoped and isolated based on scope metadata.
"""

from __future__ import annotations

import pytest
from datetime import datetime

from server.app.models import Session, SessionConfig, SessionStatus
from server.app.scoping import SessionScope
from server.app.session_store import SqliteSessionStore


@pytest.fixture
def session_store(tmp_path):
    """Create a temporary session store for testing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteSessionStore(str(workspace))
    return store


class TestSessionScope:
    """Test SessionScope class functionality."""

    def test_scope_creation(self):
        """Test creating a SessionScope with scopes."""
        scopes = {"user_id": "user123", "project": "proj456"}
        scope = SessionScope(scopes)

        assert scope.get("user_id") == "user123"
        assert scope.get("project") == "proj456"
        assert scope.get("nonexistent") is None
        assert scope.get_all() == scopes

    def test_scope_matches(self):
        """Test scope matching logic.

        The matches method checks if the current scope (filter) is a subset
        of the session scopes. So if filter has user_id=user123, and
        session has user_id=user123 and project=proj456, it matches.
        """
        filter_scope = SessionScope({"user_id": "user123"})

        # Filter matches session with same scope
        assert filter_scope.matches({"user_id": "user123"}) is True

        # Filter matches session with extra scopes
        assert filter_scope.matches({"user_id": "user123", "project": "proj456"}) is True

        # Filter doesn't match different value
        assert filter_scope.matches({"user_id": "user999"}) is False

        # Filter doesn't match missing key
        assert filter_scope.matches({"project": "proj456"}) is False

        # Empty filter matches any session
        empty_scope = SessionScope({})
        assert empty_scope.matches({"user_id": "user123"}) is True
        assert empty_scope.matches({}) is True

        # Multi-key filter
        multi_filter = SessionScope({"user_id": "user123", "project": "proj456"})
        assert multi_filter.matches({"user_id": "user123", "project": "proj456"}) is True
        assert (
            multi_filter.matches({"user_id": "user123", "project": "proj456", "team": "team1"})
            is True
        )
        assert multi_filter.matches({"user_id": "user123"}) is False  # missing project

    def test_scope_is_empty(self):
        """Test checking if scope is empty."""
        assert SessionScope({}).is_empty() is True
        assert SessionScope({"user_id": "123"}).is_empty() is False

    def test_scope_empty_matches_all(self):
        """Test that empty scope matches any session scopes."""
        empty_scope = SessionScope({})
        assert empty_scope.matches({"user_id": "123"}) is True
        assert empty_scope.matches({}) is True


class TestSessionModel:
    """Test Session model with scopes field."""

    def test_session_with_scopes(self):
        """Test creating session with scopes."""
        now = datetime.utcnow().isoformat()
        config = SessionConfig(provider="openai", model="gpt-4")
        scopes = {"user_id": "user123", "project": "proj456"}

        session = Session(
            id="test-id",
            workspace_path="/workspace",
            title="Test Session",
            thread_id="thread-123",
            status=SessionStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
            message_count=0,
            scopes=scopes,
        )

        assert session.scopes == scopes

    def test_session_to_dict_includes_scopes(self):
        """Test that to_dict includes scopes."""
        now = datetime.utcnow().isoformat()
        config = SessionConfig(provider="openai", model="gpt-4")
        scopes = {"user_id": "user123"}

        session = Session(
            id="test-id",
            workspace_path="/workspace",
            title="Test Session",
            thread_id="thread-123",
            status=SessionStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
            message_count=0,
            scopes=scopes,
        )

        data = session.to_dict()
        assert data["scopes"] == scopes

    def test_session_from_dict_includes_scopes(self):
        """Test that from_dict restores scopes."""
        data = {
            "id": "test-id",
            "workspace_path": "/workspace",
            "title": "Test Session",
            "thread_id": "thread-123",
            "status": "active",
            "config": {},
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "message_count": 0,
            "scopes": {"user_id": "user123"},
        }

        session = Session.from_dict(data)
        assert session.scopes == {"user_id": "user123"}

    def test_session_default_scopes(self):
        """Test that scopes defaults to empty dict."""
        now = datetime.utcnow().isoformat()
        config = SessionConfig()

        session = Session(
            id="test-id",
            workspace_path="/workspace",
            title="Test Session",
            thread_id="thread-123",
            status=SessionStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
            message_count=0,
        )

        assert session.scopes == {}


class TestSessionStoreScoping:
    """Test SqliteSessionStore scoping functionality."""

    @pytest.mark.asyncio
    async def test_create_session_with_scopes(self, session_store):
        """Test creating a session with scope metadata."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")
        scopes = {"user_id": "user123", "project": "proj456"}

        session = await store.create_session(
            session_id="sess-123",
            thread_id="thread-123",
            config=config,
            title="Test Session",
            scopes=scopes,
        )

        assert session.scopes == scopes

        # Verify stored correctly
        retrieved = await store.get_session("sess-123")
        assert retrieved is not None
        assert retrieved.scopes == scopes

    @pytest.mark.asyncio
    async def test_list_sessions_filtered_by_scope(self, session_store):
        """Test that list_sessions filters by scope."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")

        # Create sessions with different scopes
        await store.create_session(
            session_id="sess-user1",
            thread_id="thread-1",
            config=config,
            title="User 1 Session",
            scopes={"user_id": "user1"},
        )

        await store.create_session(
            session_id="sess-user2",
            thread_id="thread-2",
            config=config,
            title="User 2 Session",
            scopes={"user_id": "user2"},
        )

        await store.create_session(
            session_id="sess-no-scope",
            thread_id="thread-3",
            config=config,
            title="No Scope Session",
            scopes={},
        )

        # Filter by user1 scope
        user1_sessions = await store.list_sessions(filter_scopes={"user_id": "user1"})
        assert len(user1_sessions) == 1
        assert user1_sessions[0].id == "sess-user1"

        # Filter by user2 scope
        user2_sessions = await store.list_sessions(filter_scopes={"user_id": "user2"})
        assert len(user2_sessions) == 1
        assert user2_sessions[0].id == "sess-user2"

        # No filter - all sessions
        all_sessions = await store.list_sessions()
        assert len(all_sessions) == 3

    @pytest.mark.asyncio
    async def test_list_sessions_multi_dimensional_scoping(self, session_store):
        """Test filtering by multiple scope dimensions."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")

        # Create sessions with multiple scope dimensions
        await store.create_session(
            session_id="sess-1",
            thread_id="thread-1",
            config=config,
            scopes={"user_id": "user1", "project": "proj1"},
        )

        await store.create_session(
            session_id="sess-2",
            thread_id="thread-2",
            config=config,
            scopes={"user_id": "user1", "project": "proj2"},
        )

        await store.create_session(
            session_id="sess-3",
            thread_id="thread-3",
            config=config,
            scopes={"user_id": "user2", "project": "proj1"},
        )

        # Filter by both user and project
        filtered = await store.list_sessions(filter_scopes={"user_id": "user1", "project": "proj1"})
        assert len(filtered) == 1
        assert filtered[0].id == "sess-1"

        # Filter by user only
        user1_sessions = await store.list_sessions(filter_scopes={"user_id": "user1"})
        assert len(user1_sessions) == 2
        assert {s.id for s in user1_sessions} == {"sess-1", "sess-2"}

        # Filter by project only
        proj1_sessions = await store.list_sessions(filter_scopes={"project": "proj1"})
        assert len(proj1_sessions) == 2
        assert {s.id for s in proj1_sessions} == {"sess-1", "sess-3"}

    @pytest.mark.asyncio
    async def test_get_session_retrieves_scopes(self, session_store):
        """Test that get_session retrieves scope metadata."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")
        scopes = {"user_id": "user123", "team": "team456"}

        await store.create_session(
            session_id="sess-123",
            thread_id="thread-123",
            config=config,
            scopes=scopes,
        )

        retrieved = await store.get_session("sess-123")
        assert retrieved is not None
        assert retrieved.scopes == scopes

    @pytest.mark.asyncio
    async def test_session_scope_isolation(self, session_store):
        """Test that sessions are isolated by scope."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")

        # Create sessions for two different users
        await store.create_session(
            session_id="sess-a",
            thread_id="thread-a",
            config=config,
            scopes={"user_id": "alice"},
        )

        await store.create_session(
            session_id="sess-b",
            thread_id="thread-b",
            config=config,
            scopes={"user_id": "bob"},
        )

        # Alice should not see Bob's sessions
        alice_sessions = await store.list_sessions(filter_scopes={"user_id": "alice"})
        assert len(alice_sessions) == 1
        assert alice_sessions[0].id == "sess-a"
        assert all(s.scopes.get("user_id") == "alice" for s in alice_sessions)

        # Bob should not see Alice's sessions
        bob_sessions = await store.list_sessions(filter_scopes={"user_id": "bob"})
        assert len(bob_sessions) == 1
        assert bob_sessions[0].id == "sess-b"
        assert all(s.scopes.get("user_id") == "bob" for s in bob_sessions)

    @pytest.mark.asyncio
    async def test_session_without_scopes(self, session_store):
        """Test creating and retrieving sessions without scopes."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")

        session = await store.create_session(
            session_id="sess-123",
            thread_id="thread-123",
            config=config,
            title="No Scope Session",
        )

        assert session.scopes == {}

        retrieved = await store.get_session("sess-123")
        assert retrieved is not None
        assert retrieved.scopes == {}

    @pytest.mark.asyncio
    async def test_list_sessions_with_partial_scope_match(self, session_store):
        """Test that sessions must match ALL filter scopes."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")

        # Session with user_id only
        await store.create_session(
            session_id="sess-1",
            thread_id="thread-1",
            config=config,
            scopes={"user_id": "user1"},
        )

        # Session with both user_id and project
        await store.create_session(
            session_id="sess-2",
            thread_id="thread-2",
            config=config,
            scopes={"user_id": "user1", "project": "proj1"},
        )

        # Filter by both - only sess-2 matches
        filtered = await store.list_sessions(filter_scopes={"user_id": "user1", "project": "proj1"})
        assert len(filtered) == 1
        assert filtered[0].id == "sess-2"

        # Filter by user_id only - both match
        user_only = await store.list_sessions(filter_scopes={"user_id": "user1"})
        assert len(user_only) == 2


class TestScopeDependency:
    """Test scope dependency extraction from headers."""

    def test_extract_scope_from_headers(self):
        """Test extracting scope from request headers."""
        from server.app.scoping import extract_scope_from_headers
        from server.app.settings import Settings

        # Create settings with custom scope keys
        settings = Settings(
            scope_keys=["user", "project"],
            scoping_enabled=True,
            llm_provider="mock",
        )

        # Extract with all headers present
        scope = extract_scope_from_headers(
            settings,
            user="user123",
            project="proj456",
        )

        assert scope.get("user") == "user123"
        assert scope.get("project") == "proj456"

    def test_extract_scope_with_missing_headers(self):
        """Test extracting scope when some headers are missing."""
        from server.app.scoping import extract_scope_from_headers
        from server.app.settings import Settings

        settings = Settings(
            scope_keys=["user", "project"],
            scoping_enabled=False,
            llm_provider="mock",
        )

        # Only user provided
        scope = extract_scope_from_headers(
            settings,
            user="user123",
            project=None,
        )

        assert scope.get("user") == "user123"
        assert scope.get("project") is None
        assert not scope.is_empty()

    def test_extract_scope_with_defaults(self):
        """Test extracting scope with default settings (user key)."""
        from server.app.scoping import extract_scope_from_headers
        from server.app.settings import Settings

        settings = Settings(llm_provider="mock")

        # Default scope_keys is ["user"]
        scope = extract_scope_from_headers(
            settings,
            user="alice",
        )

        assert scope.get("user") == "alice"
        assert scope.get("nonexistent") is None


class TestScopingIntegration:
    """Integration tests for the complete scoping system."""

    @pytest.mark.asyncio
    async def test_session_scope_workflow(self, session_store):
        """Test complete workflow with scoped sessions."""
        store = session_store
        config = SessionConfig(provider="openai", model="gpt-4")

        # User creates session with their scope
        user_scope = {"user_id": "alice", "project": "website-redesign"}
        session = await store.create_session(
            session_id="sess-alice-1",
            thread_id="thread-1",
            config=config,
            title="Alice's Session",
            scopes=user_scope,
        )

        # Verify session stored with scope
        assert session.scopes == user_scope

        # User lists sessions and sees only theirs
        alice_sessions = await store.list_sessions(filter_scopes={"user_id": "alice"})
        assert len(alice_sessions) == 1
        assert alice_sessions[0].id == "sess-alice-1"

        # Different user sees no sessions
        bob_sessions = await store.list_sessions(filter_scopes={"user_id": "bob"})
        assert len(bob_sessions) == 0

        # Get session with scope verification
        retrieved = await store.get_session("sess-alice-1")
        assert retrieved is not None
        assert retrieved.scopes == user_scope

        # Verify scope matching works
        alice_scope = SessionScope({"user_id": "alice"})
        assert alice_scope.matches(retrieved.scopes) is True

        bob_scope = SessionScope({"user_id": "bob"})
        assert bob_scope.matches(retrieved.scopes) is False
