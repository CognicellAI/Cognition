"""Unit tests for ConfigStore-backed agent definition resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


@pytest.fixture
def store(tmp_path: Path) -> DefaultConfigStore:
    return DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)


class TestBuiltins:
    @pytest.mark.asyncio
    async def test_default_agent_present(self, store: DefaultConfigStore):
        agent = await store.get_agent_definition("default")
        assert agent is not None
        assert agent.native is True
        assert agent.mode in ("primary", "all")

    @pytest.mark.asyncio
    async def test_builtin_agents_listed(self, store: DefaultConfigStore):
        agents = await store.list_agent_definitions()
        names = [agent.name for agent in agents]
        assert "default" in names
        assert "readonly" in names
        assert "hitl_test" in names

    @pytest.mark.asyncio
    async def test_is_valid_primary_for_builtin(self, store: DefaultConfigStore):
        assert await store.is_valid_primary("default") is True
        assert await store.is_valid_primary("readonly") is True


class TestFileAgents:
    @pytest.mark.asyncio
    async def test_load_yaml_agent(self, tmp_path: Path):
        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test-agent.yaml").write_text(
            "name: test-agent\nsystem_prompt: You are a test agent.\ndescription: A test agent\nmode: primary\n"
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        agent = await store.get_agent_definition("test-agent")
        assert agent is not None
        assert agent.name == "test-agent"
        assert agent.native is False

    @pytest.mark.asyncio
    async def test_reload_file_agents_picks_up_new_file(self, tmp_path: Path):
        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        assert await store.get_agent_definition("new-agent") is None

        agents_dir = tmp_path / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "new-agent.yaml").write_text("name: new-agent\nsystem_prompt: New!\n")

        store.reload_file_agents()
        agent = await store.get_agent_definition("new-agent")
        assert agent is not None
        assert agent.system_prompt == "New!"


class TestDbAgents:
    @pytest.mark.asyncio
    async def test_upsert_agent_updates_cache(self, store: DefaultConfigStore):
        await store.upsert_agent(
            "db-agent",
            {},
            {"name": "db-agent", "system_prompt": "hello", "mode": "primary"},
            "api",
        )

        agent = await store.get_agent_definition("db-agent")
        assert agent is not None
        assert agent.native is False
        assert agent.system_prompt == "hello"

    @pytest.mark.asyncio
    async def test_delete_agent_removes_non_native_cache(self, store: DefaultConfigStore):
        await store.upsert_agent(
            "db-agent",
            {},
            {"name": "db-agent", "system_prompt": "hello", "mode": "primary"},
            "api",
        )

        deleted = await store.delete_agent("db-agent")
        assert deleted is True
        assert await store.get_agent_definition("db-agent") is None

    @pytest.mark.asyncio
    async def test_builtin_agent_not_overwritten_by_db_seed(self, store: DefaultConfigStore):
        await store.upsert_agent(
            "default",
            {},
            {"name": "default", "system_prompt": "override", "mode": "primary"},
            "api",
        )

        agent = await store.get_agent_definition("default")
        assert agent is not None
        assert agent.native is True
        assert agent.system_prompt != "override"


class TestScopePreservation:
    @pytest.mark.asyncio
    async def test_get_agent_raw_with_scope_returns_stored_scope(self, store: DefaultConfigStore):
        """get_agent_raw_with_scope returns the DB row's scope, not the definition dict's."""
        await store.upsert_agent(
            "scoped-agent",
            {"org": "acme"},
            {
                "name": "scoped-agent",
                "system_prompt": "scoped",
                "mode": "primary",
                "tools": ["my_tool"],
            },
            "api",
        )

        result = await store.get_agent_raw_with_scope("scoped-agent", {"org": "acme"})
        assert result is not None
        data, scope = result
        assert scope == {"org": "acme"}
        assert data["tools"] == ["my_tool"]

    @pytest.mark.asyncio
    async def test_get_agent_raw_with_scope_empty_scope(self, store: DefaultConfigStore):
        """Agent stored with empty scope returns {} as matched scope."""
        await store.upsert_agent(
            "unscoped-agent",
            {},
            {"name": "unscoped-agent", "system_prompt": "no scope", "mode": "primary"},
            "api",
        )

        result = await store.get_agent_raw_with_scope("unscoped-agent")
        assert result is not None
        _, scope = result
        assert scope == {}

    @pytest.mark.asyncio
    async def test_get_agent_raw_with_scope_missing_returns_none(self, store: DefaultConfigStore):
        result = await store.get_agent_raw_with_scope("no-such-agent")
        assert result is None


class TestValidationPropagation:
    @pytest.mark.asyncio
    async def test_upsert_agent_invalid_definition_raises(self, store: DefaultConfigStore):
        """Invalid agent definition should raise, not be silently swallowed.

        Regression: upsert_agent used to swallow validation errors, writing
        invalid data to the DB but not updating the in-memory cache.
        """
        with pytest.raises(Exception):
            await store.upsert_agent(
                "bad-agent",
                {},
                {"name": "bad-agent", "system_prompt": "test", "tools": [""]},
                "api",
            )

    @pytest.mark.asyncio
    async def test_upsert_agent_valid_definition_updates_cache(self, store: DefaultConfigStore):
        """Valid definition with simple tool names should succeed and update cache."""
        await store.upsert_agent(
            "simple-tool-agent",
            {},
            {
                "name": "simple-tool-agent",
                "system_prompt": "test",
                "tools": ["my_tool", "file_tools"],
            },
            "api",
        )

        agent = await store.get_agent_definition("simple-tool-agent")
        assert agent is not None
        assert agent.tools == ["my_tool", "file_tools"]
