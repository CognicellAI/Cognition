"""Unit tests for per-agent / per-session parameter overrides.

Covers two distinct override layers:

1. ``create_agent_runtime`` — ``AgentDefinition.config.recursion_limit`` overrides
   the global ``settings.agent_recursion_limit`` when explicitly set.

2. ``DeepAgentService`` session-config merge — ``SessionConfig.max_tokens`` and
   ``SessionConfig.recursion_limit`` are applied to the copied ``llm_settings``
   before the runtime is created.

3. ``SessionConfig`` round-trips — ``recursion_limit`` survives to_dict / from_dict.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.definition import AgentConfig, AgentDefinition
from server.app.models import Session, SessionConfig, SessionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_definition(recursion_limit: int | None = None) -> AgentDefinition:
    """Build a minimal AgentDefinition with an optional recursion_limit override."""
    return AgentDefinition(
        name="test-agent",
        system_prompt="You are a test assistant.",
        config=AgentConfig(recursion_limit=recursion_limit),
    )


def _make_session(
    max_tokens: int | None = None,
    recursion_limit: int | None = None,
) -> Session:
    """Build a Session with configurable config overrides."""
    return Session(
        id="sess-001",
        workspace_path="/tmp/workspace",
        title="Test Session",
        thread_id="thread-001",
        status=SessionStatus.ACTIVE,
        config=SessionConfig(
            provider="mock",
            model="mock-model",
            max_tokens=max_tokens,
            recursion_limit=recursion_limit,
        ),
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# create_agent_runtime — recursion_limit override
# ---------------------------------------------------------------------------


class TestCreateAgentRuntimeRecursionLimit:
    """create_agent_runtime must honour per-agent recursion_limit overrides."""

    @pytest.mark.asyncio
    async def test_uses_global_default_when_definition_not_set(self):
        """When definition.config.recursion_limit is None, the GlobalAgentDefaults value (1000) wins.

        agent_recursion_limit was moved from Settings to GlobalAgentDefaults (ConfigRegistry).
        The hardcoded fallback in create_agent_runtime now mirrors that default.
        """
        definition = _make_definition(recursion_limit=None)

        mock_settings = MagicMock()
        mock_settings.trusted_tool_namespaces = ["server.app.tools"]

        mock_runtime = MagicMock()

        with (
            patch(
                "server.app.agent.runtime.DeepAgentRuntime",
                return_value=mock_runtime,
            ) as mock_runtime_cls,
            patch("server.app.agent.runtime.create_storage_backend") as mock_storage,
            patch("server.app.agent.runtime.create_cognition_agent", new_callable=AsyncMock),
        ):
            mock_storage.return_value.get_checkpointer = AsyncMock(return_value=MagicMock())

            from server.app.agent.runtime import create_agent_runtime

            await create_agent_runtime(
                definition=definition,
                workspace_path="/tmp/workspace",
                thread_id="thread-001",
                settings=mock_settings,
            )

        _, kwargs = mock_runtime_cls.call_args
        assert kwargs["recursion_limit"] == 1000, (
            "Should fall back to GlobalAgentDefaults.recursion_limit (1000) when definition has no override"
        )

    @pytest.mark.asyncio
    async def test_definition_recursion_limit_overrides_settings(self):
        """When definition.config.recursion_limit is set, it overrides settings."""
        definition = _make_definition(recursion_limit=200)

        mock_settings = MagicMock()
        mock_settings.agent_recursion_limit = 1000  # higher default — must be overridden
        mock_settings.trusted_tool_namespaces = ["server.app.tools"]

        mock_runtime = MagicMock()

        with (
            patch(
                "server.app.agent.runtime.DeepAgentRuntime",
                return_value=mock_runtime,
            ) as mock_runtime_cls,
            patch("server.app.agent.runtime.create_storage_backend") as mock_storage,
            patch("server.app.agent.runtime.create_cognition_agent", new_callable=AsyncMock),
        ):
            mock_storage.return_value.get_checkpointer = AsyncMock(return_value=MagicMock())

            from server.app.agent.runtime import create_agent_runtime

            await create_agent_runtime(
                definition=definition,
                workspace_path="/tmp/workspace",
                thread_id="thread-001",
                settings=mock_settings,
            )

        _, kwargs = mock_runtime_cls.call_args
        assert kwargs["recursion_limit"] == 200, (
            "definition.config.recursion_limit must override settings.agent_recursion_limit"
        )

    @pytest.mark.asyncio
    async def test_zero_is_not_valid_so_none_is_the_neutral_value(self):
        """recursion_limit of None (not 0) signals 'use default'. Pydantic enforces gt=0."""
        with pytest.raises(ValueError):
            AgentConfig(recursion_limit=0)

        with pytest.raises(ValueError):
            AgentConfig(recursion_limit=-1)


# ---------------------------------------------------------------------------
# SessionConfig — round-trip
# ---------------------------------------------------------------------------


class TestSessionConfigRoundTrip:
    """SessionConfig.recursion_limit must survive to_dict / from_dict cycles."""

    def test_recursion_limit_in_to_dict(self):
        """recursion_limit is included in the serialised dict."""
        session = _make_session(max_tokens=4096, recursion_limit=300)
        data = session.to_dict()
        assert data["config"]["recursion_limit"] == 300

    def test_recursion_limit_none_in_to_dict(self):
        """recursion_limit=None is serialised as None (not omitted)."""
        session = _make_session()
        data = session.to_dict()
        assert "recursion_limit" in data["config"]
        assert data["config"]["recursion_limit"] is None

    def test_recursion_limit_from_dict(self):
        """Session.from_dict must restore recursion_limit correctly."""
        session = _make_session(max_tokens=8192, recursion_limit=750)
        data = session.to_dict()
        restored = Session.from_dict(data)
        assert restored.config.recursion_limit == 750

    def test_recursion_limit_missing_from_dict_defaults_to_none(self):
        """Older stored sessions without recursion_limit must deserialise cleanly."""
        data = {
            "id": "sess-001",
            "workspace_path": "/tmp/workspace",
            "title": "Old Session",
            "thread_id": "thread-001",
            "status": "active",
            "config": {
                "provider": "mock",
                "model": "mock-model",
                "max_tokens": 4096,
                # recursion_limit deliberately absent — simulates old serialised data
            },
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        session = Session.from_dict(data)
        assert session.config.recursion_limit is None

    def test_max_tokens_round_trips(self):
        """max_tokens survives to_dict / from_dict (regression guard)."""
        session = _make_session(max_tokens=16000)
        data = session.to_dict()
        restored = Session.from_dict(data)
        assert restored.config.max_tokens == 16000


# ---------------------------------------------------------------------------
# MemoryStorageBackend — update_session merges recursion_limit
# ---------------------------------------------------------------------------


class TestMemoryStorageBackendMerge:
    """MemoryStorageBackend.update_session must merge recursion_limit correctly."""

    @pytest.mark.asyncio
    async def test_recursion_limit_update_applied(self):
        """Updating a session with recursion_limit=300 stores 300."""
        from server.app.storage.memory import MemoryStorageBackend

        backend = MemoryStorageBackend()
        await backend.create_session(
            session_id="sess-merge",
            thread_id="thread-merge",
            config=SessionConfig(provider="mock", model="mock"),
        )

        updated = await backend.update_session(
            session_id="sess-merge",
            config=SessionConfig(recursion_limit=300),
        )

        assert updated is not None
        assert updated.config.recursion_limit == 300

    @pytest.mark.asyncio
    async def test_recursion_limit_none_preserves_existing(self):
        """Passing recursion_limit=None in the update must not clear an existing value."""
        from server.app.storage.memory import MemoryStorageBackend

        backend = MemoryStorageBackend()
        await backend.create_session(
            session_id="sess-preserve",
            thread_id="thread-preserve",
            config=SessionConfig(provider="mock", model="mock", recursion_limit=500),
        )

        # Update with an unrelated field — recursion_limit must remain 500
        updated = await backend.update_session(
            session_id="sess-preserve",
            config=SessionConfig(max_tokens=4096),
        )

        assert updated is not None
        assert updated.config.recursion_limit == 500

    @pytest.mark.asyncio
    async def test_max_tokens_update_applied(self):
        """Updating a session with max_tokens stores the new value."""
        from server.app.storage.memory import MemoryStorageBackend

        backend = MemoryStorageBackend()
        await backend.create_session(
            session_id="sess-mt",
            thread_id="thread-mt",
            config=SessionConfig(provider="mock", model="mock"),
        )

        updated = await backend.update_session(
            session_id="sess-mt",
            config=SessionConfig(max_tokens=8192),
        )

        assert updated is not None
        assert updated.config.max_tokens == 8192
