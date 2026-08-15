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

from server.app.storage.common import effective_scope_key
from server.app.storage.config_models import (
    GlobalAgentDefaults,
    GlobalProviderDefaults,
    ProviderConfig,
    SandboxProfile,
    ToolRegistration,
)
from server.app.storage.config_registry import (
    MemoryConfigRegistry,
    PostgresConfigRegistry,
    SqliteConfigRegistry,
)

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


def _tool(name: str = "mytool", scope: dict | None = None) -> ToolRegistration:
    return ToolRegistration(
        name=name,
        path=f"server.app.tools.{name}",
        scope=scope or {},
        source="api",
    )


def _sandbox_profile(
    name: str = "default",
    scope: dict | None = None,
    image_arn: str = "arn:aws:lambda:us-east-1:123456789012:microvm-image:python-agent",
) -> SandboxProfile:
    return SandboxProfile(
        name=name,
        image_arn=image_arn,
        scope=scope or {},
        source="api",
    )


# ---------------------------------------------------------------------------
# SandboxProfile validation
# ---------------------------------------------------------------------------


class TestSandboxProfileValidation:
    def test_vpc_egress_requires_connector_arns(self):
        with pytest.raises(ValueError, match="egress_network_connector_arns"):
            SandboxProfile(
                name="vpc-profile",
                image_arn="arn:aws:lambda:us-east-1:123456789012:microvm-image:python-agent",
                egress_mode="vpc",
            )

    def test_image_must_be_lambda_microvm_arn(self):
        with pytest.raises(ValueError, match="image_arn"):
            SandboxProfile(
                name="bad-image",
                image_arn="arn:aws:ecr:us-east-1:123456789012:repository/runtime",
            )

    def test_logging_must_select_one_destination(self):
        with pytest.raises(ValueError, match="exactly one"):
            SandboxProfile(
                name="bad-logging",
                image_arn=("arn:aws:lambda:us-east-1:123456789012:microvm-image:python-agent"),
                logging={
                    "disabled": {},
                    "cloud_watch": {"log_group": "/aws/lambda-microvms/cognition"},
                },
            )

    def test_cloudwatch_logging_profile_is_valid(self):
        profile = SandboxProfile(
            name="cloudwatch-logging",
            image_arn="arn:aws:lambda:us-east-1:123456789012:microvm-image:python-agent",
            logging={
                "cloud_watch": {
                    "log_group": "/aws/lambda-microvms/cognition",
                    "log_stream": "agent-session",
                }
            },
        )

        assert profile.logging is not None
        assert profile.logging.to_aws_request() == {
            "cloudWatch": {
                "logGroup": "/aws/lambda-microvms/cognition",
                "logStream": "agent-session",
            }
        }


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
        updated = ProviderConfig(
            id="prov-1",
            provider="bedrock",
            model="anthropic.claude-3-sonnet-20240229-v1:0",
            region="us-east-1",
            source="api",
        )
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
# MemoryConfigRegistry — Sandbox profile CRUD
# ---------------------------------------------------------------------------


class TestMemorySandboxProfileCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_sandbox_profile(_sandbox_profile())
        result = await mem_reg.get_sandbox_profile("default")
        assert result is not None
        assert result.backend == "aws_lambda_microvm"
        assert result.egress_mode == "internet"

    @pytest.mark.asyncio
    async def test_scoped_profile_wins_over_global(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_sandbox_profile(
            _sandbox_profile(
                "agent-runtime",
                image_arn=("arn:aws:lambda:us-east-1:123456789012:microvm-image:global"),
            )
        )
        await mem_reg.upsert_sandbox_profile(
            _sandbox_profile(
                "agent-runtime",
                scope={"tenant": "acme"},
                image_arn="arn:aws:lambda:us-east-1:123456789012:microvm-image:acme",
            )
        )

        result = await mem_reg.get_sandbox_profile(
            "agent-runtime",
            scope={"tenant": "acme"},
        )
        assert result is not None
        assert result.image_arn.endswith(":acme")

    @pytest.mark.asyncio
    async def test_delete_profile(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_sandbox_profile(_sandbox_profile())
        deleted = await mem_reg.delete_sandbox_profile("default")
        assert deleted is True
        assert await mem_reg.get_sandbox_profile("default") is None


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Scope resolution
# ---------------------------------------------------------------------------


class TestMemoryScopeResolution:
    @pytest.mark.asyncio
    async def test_global_row_matches_any_scope(self, mem_reg: MemoryConfigRegistry):
        """A row with scope={} should be returned for any scope query."""
        await mem_reg.upsert_tool(_tool("global-tool", scope={}))
        result = await mem_reg.get_tool("global-tool", scope={"user": "alice"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_scoped_row_wins_over_global(self, mem_reg: MemoryConfigRegistry):
        """More-specific scope wins over global row for the same name."""
        await mem_reg.upsert_tool(_tool("typed-tool", scope={}))
        scoped_tool = _tool("typed-tool", scope={"user": "alice"}).model_copy(
            update={"path": "server.app.tools.scoped"}
        )
        await mem_reg.upsert_tool(scoped_tool)

        result = await mem_reg.get_tool("typed-tool", scope={"user": "alice"})
        assert result is not None
        assert result.path == "server.app.tools.scoped"

    @pytest.mark.asyncio
    async def test_scoped_row_invisible_to_other_users(self, mem_reg: MemoryConfigRegistry):
        """A user-scoped row is not visible when querying a different user's scope."""
        await mem_reg.upsert_tool(_tool("private-tool", scope={"user": "alice"}))
        result = await mem_reg.get_tool("private-tool", scope={"user": "bob"})
        assert result is None

    @pytest.mark.asyncio
    async def test_list_only_returns_visible_rows(self, mem_reg: MemoryConfigRegistry):
        """list_tools respects scope — only visible rows returned."""
        await mem_reg.upsert_tool(_tool("global-tool", scope={}))
        await mem_reg.upsert_tool(_tool("alice-tool", scope={"user": "alice"}))
        assert [tool.name for tool in await mem_reg.list_tools(scope={"user": "bob"})] == [
            "global-tool"
        ]
        names = {tool.name for tool in await mem_reg.list_tools(scope={"user": "alice"})}
        assert names == {"global-tool", "alice-tool"}


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
            "tool",
            "seed-tool",
            {},
            {"name": "seed-tool", "path": "server.app.tools.seed"},
            "file",
        )
        assert inserted is True
        result = await mem_reg.get_tool("seed-tool")
        assert result is not None

    @pytest.mark.asyncio
    async def test_seed_if_absent_does_not_overwrite(self, mem_reg: MemoryConfigRegistry):
        """Seeding should not overwrite an existing row."""
        await mem_reg.upsert_tool(_tool("existing"))
        inserted = await mem_reg.seed_if_absent(
            "tool",
            "existing",
            {},
            {"name": "existing", "path": "server.app.tools.override"},
            "file",
        )
        assert inserted is False
        result = await mem_reg.get_tool("existing")
        assert result is not None
        assert result.path == "server.app.tools.existing"


# ---------------------------------------------------------------------------
# MemoryConfigRegistry — Change log
# ---------------------------------------------------------------------------


class TestMemoryChangeLog:
    @pytest.mark.asyncio
    async def test_changes_recorded_on_upsert(self, mem_reg: MemoryConfigRegistry):
        before = datetime.now(UTC) - timedelta(seconds=1)
        await mem_reg.upsert_tool(_tool())
        changes = await mem_reg.get_changes_since(before)
        assert len(changes) >= 1
        assert any(c.name == "mytool" for c in changes)

    @pytest.mark.asyncio
    async def test_changes_recorded_on_delete(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_tool(_tool())
        before = datetime.now(UTC) - timedelta(seconds=1)
        await mem_reg.delete_tool("mytool")
        changes = await mem_reg.get_changes_since(before)
        delete_changes = [c for c in changes if c.operation == "delete"]
        assert len(delete_changes) >= 1

    @pytest.mark.asyncio
    async def test_get_changes_since_filters_by_time(self, mem_reg: MemoryConfigRegistry):
        await mem_reg.upsert_tool(_tool("old-tool"))
        cutoff = datetime.now(UTC)
        await mem_reg.upsert_tool(_tool("new-tool"))

        changes = await mem_reg.get_changes_since(cutoff)
        names = {c.name for c in changes}
        assert "new-tool" in names
        assert "old-tool" not in names


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
    async def test_upsert_and_list_tools(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            await reg.upsert_tool(_tool("t1"))
            await reg.upsert_tool(_tool("t2"))
            names = {tool.name for tool in await reg.list_tools()}
            assert {"t1", "t2"} <= names
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
    async def test_inherited_config_resolution_uses_scope_key_indexes(self, tmp_path: Path):
        import sqlite3

        db_path = tmp_path / "indexed-config.db"
        reg = SqliteConfigRegistry(str(db_path))
        target_scope = {"tenant": "acme", "project": "red"}
        candidate_keys = [
            effective_scope_key({}),
            effective_scope_key({"project": "red"}),
            effective_scope_key({"tenant": "acme"}),
            effective_scope_key(target_scope),
        ]
        placeholders = ",".join("?" for _ in candidate_keys)
        try:
            await reg.upsert_tool(_tool("shared", scope={}))
            await reg.upsert_tool(_tool("tenant-only", scope={"tenant": "acme"}))
            await reg.upsert_tool(
                _tool("shared", scope=target_scope).model_copy(
                    update={"path": "server.app.tools.exact"}
                )
            )
            for index in range(150):
                await reg.upsert_tool(
                    _tool(f"noise-{index}", scope={"tenant": f"other-{index}"})
                )

            result = await reg.get_tool("shared", target_scope)
            assert result is not None
            assert result.path == "server.app.tools.exact"

            visible = await reg.list_tools(target_scope)
            visible_names = {tool.name for tool in visible}
            assert {"shared", "tenant-only"} <= visible_names
            assert all(not name.startswith("noise-") for name in visible_names)

            with sqlite3.connect(db_path) as conn:
                exact_plan = conn.execute(
                    f"""
                    EXPLAIN QUERY PLAN
                    SELECT scope, definition
                    FROM config_entities
                    WHERE entity_type=? AND name=? AND scope_key IN ({placeholders})
                    """,
                    ("tool", "shared", *candidate_keys),
                ).fetchall()
                list_plan = conn.execute(
                    f"""
                    EXPLAIN QUERY PLAN
                    SELECT name, scope, definition
                    FROM config_entities
                    WHERE entity_type=? AND scope_key IN ({placeholders})
                    """,
                    ("tool", *candidate_keys),
                ).fetchall()

            exact_plan_text = " ".join(str(row) for row in exact_plan)
            list_plan_text = " ".join(str(row) for row in list_plan)
            assert (
                "idx_config_entities_lookup" in exact_plan_text
                or "idx_config_entities_scope_list" in exact_plan_text
            )
            assert "idx_config_entities_scope_list" in list_plan_text
            assert "SCAN config_entities" not in exact_plan_text
            assert "SCAN config_entities" not in list_plan_text
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_delete_returns_false_for_missing(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            deleted = await reg.delete_tool("no-such-tool")
            assert deleted is False
        finally:
            await reg.close()

    @pytest.mark.asyncio
    async def test_seed_if_absent_does_not_overwrite(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            await reg.upsert_tool(_tool("existing"))
            inserted = await reg.seed_if_absent(
                "tool",
                "existing",
                {},
                {"name": "existing", "path": "server.app.tools.new"},
                "file",
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

    @pytest.mark.asyncio
    async def test_sandbox_profile_round_trip(self, tmp_path: Path):
        reg = _make_sqlite_reg(tmp_path)
        try:
            profile = _sandbox_profile("lambda-default")
            await reg.upsert_sandbox_profile(profile)
            result = await reg.get_sandbox_profile("lambda-default")
            assert result is not None
            assert result.image_arn == profile.image_arn
        finally:
            await reg.close()


class TestPostgresConfigRegistry:
    @pytest.mark.asyncio
    async def test_delete_returns_false_for_missing_rowcount(self):
        class _Cursor:
            rowcount = 0

        class _Conn:
            async def execute(self, *_args, **_kwargs):
                return _Cursor()

        class _ConnectionContext:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *_args):
                return None

        class _Pool:
            def connection(self):
                return _ConnectionContext()

        reg = PostgresConfigRegistry("postgresql://example/test")
        reg._pool = _Pool()  # type: ignore[assignment]

        deleted = await reg.delete_tool("missing-tool")

        assert deleted is False
