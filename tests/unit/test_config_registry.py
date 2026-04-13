"""Unit tests for ConfigRegistry implementations.

Covers:
- MemoryConfigRegistry: full CRUD, scope resolution, seeding, change log
- SqliteConfigRegistry: CRUD + scope resolution (uses tmp db file)
- Protocol conformance
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.storage.config_models import (
    GlobalAgentDefaults,
    GlobalProviderDefaults,
    ProviderConfig,
    SkillDefinition,
    ToolRegistration,
)
from server.app.storage.config_registry import MemoryConfigRegistry, SqliteConfigRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_reg() -> MemoryConfigRegistry:
    """Fresh in-memory registry for each test."""
    return MemoryConfigRegistry()


def _provider(id: str = "prov-1", scope: dict | None = None) -> ProviderConfig:
    return ProviderConfig(
        id=id,
        provider="openai",
        model="gpt-4o",
        scope=scope or {},
        source="api",
    )


def _skill(name: str = "myskill", scope: dict | None = None) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        path=f".cognition/skills/{name}.md",
        scope=scope or {},
        source="api",
    )


def _tool(name: str = "mytool", scope: dict | None = None) -> ToolRegistration:
    return ToolRegistration(
        name=name,
        path=f"server.app.tools.{name}",
        scope=scope or {},
        source="api",
    )


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Provider CRUD
# ---------------------------------------------------------------------------


class TestMemoryProviderCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, mem_reg: MemoryConfigRegistry):
        prov = _provider()
        await mem_reg.upsert_provider(prov)
        result = await mem_reg.get_provider("prov-1")
        assert result is not None
        assert result.id == "prov-1"
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, mem_reg: MemoryConfigRegistry):
        prov = _provider()
        await mem_reg.upsert_provider(prov)
        updated = ProviderConfig(id="prov-1", provider="bedrock", model="claude-3", source="api")
        await mem_reg.upsert_provider(updated)
        result = await mem_reg.get_provider("prov-1")
        assert result is not None
        assert result.provider == "bedrock"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, mem_reg: MemoryConfigRegistry):
        result = await mem_reg.get_provider("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_existing(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_provider(_provider())
        deleted = await mem_reg.delete_provider("prov-1")
        assert deleted is True
        assert await mem_reg.get_provider("prov-1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, mem_reg: MemoryConfigRegistry):
        result = await mem_reg.delete_provider("ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_providers(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_provider(_provider("p1"))
        await mem_reg.upsert_provider(_provider("p2"))
        providers = await mem_reg.list_providers()
        names = {p.id for p in providers}
        assert "p1" in names
        assert "p2" in names


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Skill CRUD
# ---------------------------------------------------------------------------


class TestMemorySkillCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_skill(_skill())
        result = await mem_reg.get_skill("myskill")
        assert result is not None
        assert result.name == "myskill"

    @pytest.mark.asyncio
    async def test_delete_skill(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_skill(_skill())
        deleted = await mem_reg.delete_skill("myskill")
        assert deleted is True
        assert await mem_reg.get_skill("myskill") is None

    @pytest.mark.asyncio
    async def test_list_skills_empty(self, mem_reg: MemoryConfigRegistry):
        skills = await mem_reg.list_skills()
        assert skills == []


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Tool CRUD
# ---------------------------------------------------------------------------


class TestMemoryToolCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_tool(_tool())
        result = await mem_reg.get_tool("mytool")
        assert result is not None
        assert result.name == "mytool"

    @pytest.mark.asyncio
    async def test_delete_tool(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_tool(_tool())
        deleted = await mem_reg.delete_tool("mytool")
        assert deleted is True
        assert await mem_reg.get_tool("mytool") is None


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Scope resolution
# ---------------------------------------------------------------------------


class TestMemoryScopeResolution:
    @pytest.mark.asyncio
    async def test_global_row_matches_any_scope(self, mem_reg: MemoryConfigRegistry):
        """A row with scope={} should be returned for any scope query."""
        await mem_reg.upsert_skill(_skill("global-skill", scope={}))
        result = await mem_reg.get_skill("global-skill", scope={"user": "alice"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_scoped_row_wins_over_global(self, mem_reg: MemoryConfigRegistry):
        """More-specific scope wins over global row for the same name."""
        global_skill = SkillDefinition(
            name="typed-skill", path="global.md", description="global", scope={}, source="api"
        )
        scoped_skill = SkillDefinition(
            name="typed-skill",
            path="scoped.md",
            description="scoped",
            scope={"user": "alice"},
            source="api",
        )
        await mem_reg.upsert_skill(global_skill)
        await mem_reg.upsert_skill(scoped_skill)

        result = await mem_reg.get_skill("typed-skill", scope={"user": "alice"})
        assert result is not None
        assert result.path == "scoped.md"

    @pytest.mark.asyncio
    async def test_scoped_row_invisible_to_other_users(self, mem_reg: MemoryConfigRegistry):
        """A user-scoped row is not visible when querying a different user's scope."""
        scoped = SkillDefinition(
            name="private-skill",
            path="private.md",
            scope={"user": "alice"},
            source="api",
        )
        await mem_reg.upsert_skill(scoped)

        result = await mem_reg.get_skill("private-skill", scope={"user": "bob"})
        assert result is None

    @pytest.mark.asyncio
    async def test_list_only_returns_visible_rows(self, mem_reg: MemoryConfigRegistry):
        """list_skills respects scope — only visible rows returned."""
        await mem_reg.upsert_skill(
            SkillDefinition(name="global-s", path="g.md", scope={}, source="api")
        )
        await mem_reg.upsert_skill(
            SkillDefinition(name="alice-s", path="a.md", scope={"user": "alice"}, source="api")
        )
        # Bob should see only global
        bob_skills = await mem_reg.list_skills(scope={"user": "bob"})
        assert len(bob_skills) == 1
        assert bob_skills[0].name == "global-s"

        # Alice should see both (global + her own)
        alice_skills = await mem_reg.list_skills(scope={"user": "alice"})
        names = {s.name for s in alice_skills}
        assert "global-s" in names
        assert "alice-s" in names


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Global defaults
# ---------------------------------------------------------------------------


class TestMemoryGlobalDefaults:
    @pytest.mark.asyncio
    async def test_default_provider_defaults(self, mem_reg: MemoryConfigRegistry):
        defaults = await mem_reg.get_global_provider_defaults()
        assert isinstance(defaults, GlobalProviderDefaults)
        assert defaults.provider == "openai_compatible"

    @pytest.mark.asyncio
    async def test_set_and_get_global_provider_defaults(self, mem_reg: MemoryConfigRegistry):
        d = GlobalProviderDefaults(provider="openai", model="gpt-4o-mini", max_tokens=8000)
        await mem_reg.set_global_provider_defaults(d)
        result = await mem_reg.get_global_provider_defaults()
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"
        assert result.max_tokens == 8000

    @pytest.mark.asyncio
    async def test_default_agent_defaults(self, mem_reg: MemoryConfigRegistry):
        defaults = await mem_reg.get_global_agent_defaults()
        assert isinstance(defaults, GlobalAgentDefaults)
        assert defaults.recursion_limit == 1000


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Seeding
# ---------------------------------------------------------------------------


class TestMemorySeeding:
    @pytest.mark.asyncio
    async def test_seed_if_absent_inserts_when_missing(self, mem_reg: MemoryConfigRegistry):
        inserted = await mem_reg.seed_if_absent(
            "skill", "seed-skill", {}, {"name": "seed-skill", "path": "x.md"}, "file"
        )
        assert inserted is True
        result = await mem_reg.get_skill("seed-skill")
        assert result is not None

    @pytest.mark.asyncio
    async def test_seed_if_absent_does_not_overwrite(self, mem_reg: MemoryConfigRegistry):
        """Seeding should not overwrite an existing row."""
        await mem_reg.upsert_skill(
            SkillDefinition(name="existing", path="original.md", source="api")
        )
        inserted = await mem_reg.seed_if_absent(
            "skill",
            "existing",
            {},
            {"name": "existing", "path": "override.md"},
            "file",
        )
        assert inserted is False
        result = await mem_reg.get_skill("existing")
        assert result is not None
        assert result.path == "original.md"


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Change log
# ---------------------------------------------------------------------------


class TestMemoryChangeLog:
    @pytest.mark.asyncio
    async def test_changes_recorded_on_upsert(self, mem_reg: MemoryConfigRegistry):
        before = datetime.now(UTC) - timedelta(seconds=1)
        await mem_reg.upsert_skill(_skill())
        changes = await mem_reg.get_changes_since(before)
        assert len(changes) >= 1
        assert any(c.name == "myskill" for c in changes)

    @pytest.mark.asyncio
    async def test_changes_recorded_on_delete(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_skill(_skill())
        before = datetime.now(UTC) - timedelta(seconds=1)
        await mem_reg.delete_skill("myskill")
        changes = await mem_reg.get_changes_since(before)
        delete_changes = [c for c in changes if c.operation == "delete"]
        assert len(delete_changes) >= 1

    @pytest.mark.asyncio
    async def test_get_changes_since_filters_by_time(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_skill(_skill("old-skill"))
        cutoff = datetime.now(UTC)
        await mem_reg.upsert_skill(_skill("new-skill"))

        changes = await mem_reg.get_changes_since(cutoff)
        names = {c.name for c in changes}
        assert "new-skill" in names
        assert "old-skill" not in names


# ---------------------------------------------------------------------------
# SqliteConfigRegistry — basic smoke tests (mirrors Memory tests for key ops)
# ---------------------------------------------------------------------------
# Each test creates a fresh SqliteConfigRegistry with its own DB file and
# calls close() after the test to release the cached aiosqlite connection.


def _make_sqlite_reg(tmp_path: Path, suffix: str = "") -> SqliteConfigRegistry:
    """Create a fresh SqliteConfigRegistry with schema pre-created via sync sqlite3."""
    db_path = str(tmp_path / f"config{suffix}.db")
    reg = SqliteConfigRegistry(db_path)
    # Use synchronous sqlite3 for schema to avoid aiosqlite thread issues
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '{}',
            definition TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'file',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_type, name, scope)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '{}',
            operation TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()
    return reg


class TestSqliteConfigRegistry:
    """Smoke-test the SQLite implementation for the most critical operations.

    Each test creates its own fresh DB to avoid cross-test contamination.
    """

    @pytest.mark.asyncio
    async def test_upsert_and_get_provider(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            prov = _provider()
            await reg.upsert_provider(prov)
            result = await reg.get_provider("prov-1")
            assert result is not None
            assert result.model == "gpt-4o"
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_upsert_and_list_skills(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            await reg.upsert_skill(_skill("s1"))
            await reg.upsert_skill(_skill("s2"))
            skills = await reg.list_skills()
            names = {s.name for s in skills}
            assert "s1" in names
            assert "s2" in names
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_scoped_resolution(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            global_t = ToolRegistration(name="t", path="global.py", scope={}, source="file")
            scoped_t = ToolRegistration(
                name="t", path="scoped.py", scope={"user": "alice"}, source="api"
            )
            await reg.upsert_tool(global_t)
            await reg.upsert_tool(scoped_t)

            result = await reg.get_tool("t", scope={"user": "alice"})
            assert result is not None
            assert result.path == "scoped.py"
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_delete_returns_false_for_missing(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            deleted = await reg.delete_skill("no-such-skill")
            assert deleted is False
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_seed_if_absent_does_not_overwrite(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            await reg.upsert_skill(SkillDefinition(name="sk", path="orig.md", source="api"))
            inserted = await reg.seed_if_absent(
                "skill", "sk", {}, {"name": "sk", "path": "new.md"}, "file"
            )
            assert inserted is False
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_global_defaults_round_trip(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            d = GlobalProviderDefaults(provider="bedrock", model="claude-3", max_tokens=4096)
            await reg.set_global_provider_defaults(d)
            result = await reg.get_global_provider_defaults()
            assert result.provider == "bedrock"
            assert result.model == "claude-3"
        finally:
            await reg.close()
