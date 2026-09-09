"""Clean-database migration checks for the server schema."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import aiosqlite

from server.app.protocols.a2a.card import build_agent_card_for_agent
from server.app.storage.common import canonical_json_digest, effective_scope_key
from server.app.storage.config_registry import SqliteConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


async def _run_alembic(database: Path, *args: str) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["COGNITION_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "server/alembic.ini",
        *args,
        cwd=Path(__file__).parents[2],
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return int(process.returncode or 0), (stdout + stderr).decode()


async def test_alembic_upgrade_head_creates_runtime_task_schema(tmp_path: Path) -> None:
    """A base installation can migrate a fresh SQLite database to head."""
    database = tmp_path / "migration.db"
    returncode, output = await _run_alembic(database, "upgrade", "head")
    assert returncode == 0, output

    async with aiosqlite.connect(database) as connection:
        async with connection.execute("SELECT version_num FROM alembic_version") as cursor:
            version = await cursor.fetchone()
        async with connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ) as cursor:
            tables = {str(row[0]) async for row in cursor}
        async with connection.execute("PRAGMA table_info(session_runs)") as cursor:
            run_columns = {str(row[1]) async for row in cursor}
        async with connection.execute("PRAGMA table_info(session_events)") as cursor:
            event_columns = {str(row[1]) async for row in cursor}
        async with connection.execute("PRAGMA table_info(config_entities)") as cursor:
            config_columns = {str(row[1]) async for row in cursor}
        async with connection.execute("PRAGMA table_info(artifacts)") as cursor:
            artifact_columns = {str(row[1]) async for row in cursor}

    assert version is not None
    assert version[0] == "007"
    assert "runtime_tasks" in tables
    assert "task_id" in run_columns
    assert {"scope_key", "agent_revision", "runtime_manifest", "manifest_digest"} <= run_columns
    assert "task_id" in event_columns
    assert "scope_key" in event_columns
    assert {"scope_key", "revision", "definition_digest"} <= config_columns
    assert {"path", "object_key", "content_checksum", "content_size"} <= artifact_columns


async def test_v013_migration_backfills_scope_keys_and_manifests(
    tmp_path: Path,
) -> None:
    """Revision 005 upgrades a legacy exact-scope schema in place."""
    database = tmp_path / "legacy-v012.db"
    scope = {"tenant": "migration", "project": "alpha"}
    definition = {
        "name": "migration-agent",
        "mode": "primary",
        "system_prompt": "Migrated agent.",
    }
    async with aiosqlite.connect(database) as connection:
        await connection.executescript(
            """
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            INSERT INTO alembic_version (version_num) VALUES ('004');

            CREATE TABLE sessions (
                id VARCHAR(36) PRIMARY KEY,
                workspace_path TEXT NOT NULL,
                title TEXT,
                thread_id VARCHAR(36) NOT NULL,
                status VARCHAR(20) NOT NULL,
                config JSON NOT NULL,
                scopes JSON,
                metadata JSON,
                message_count INTEGER,
                agent_name VARCHAR(100) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE session_runs (
                id VARCHAR(36) PRIMARY KEY,
                session_id VARCHAR(36) NOT NULL,
                thread_id VARCHAR(100) NOT NULL,
                task_id VARCHAR(36),
                status VARCHAR(30) NOT NULL,
                effective_scope JSON NOT NULL,
                idempotency_key VARCHAR(200),
                attempt INTEGER NOT NULL,
                parent_run_id VARCHAR(36),
                started_at DATETIME,
                last_activity_at DATETIME,
                completed_at DATETIME,
                error_code VARCHAR(100),
                status_reason TEXT,
                trace_id VARCHAR(100),
                metadata JSON NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE session_events (
                id VARCHAR(36) PRIMARY KEY,
                session_id VARCHAR(36) NOT NULL,
                run_id VARCHAR(36) NOT NULL,
                task_id VARCHAR(36),
                sequence INTEGER NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                visibility VARCHAR(30) NOT NULL,
                payload JSON NOT NULL,
                effective_scope JSON NOT NULL,
                trace_id VARCHAR(100),
                span_id VARCHAR(100),
                created_at DATETIME NOT NULL
            );
            CREATE TABLE artifacts (
                id VARCHAR(100) NOT NULL,
                name TEXT NOT NULL,
                artifact_type VARCHAR(50) NOT NULL,
                scope JSON NOT NULL,
                version INTEGER NOT NULL,
                data BLOB,
                metadata JSON NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE config_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type VARCHAR(50) NOT NULL,
                name VARCHAR(200) NOT NULL,
                scope JSON NOT NULL,
                definition JSON NOT NULL,
                source VARCHAR(10) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE config_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type VARCHAR(50) NOT NULL,
                name VARCHAR(200) NOT NULL,
                scope JSON NOT NULL,
                operation VARCHAR(10) NOT NULL,
                changed_at DATETIME NOT NULL,
                processed BOOLEAN NOT NULL
            );
            """
        )
        await connection.execute(
            """
            INSERT INTO sessions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-1",
                "/workspace",
                "Legacy",
                "thread-1",
                "active",
                "{}",
                '{"tenant":"migration","project":"alpha"}',
                "{}",
                0,
                "migration-agent",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        await connection.execute(
            """
            INSERT INTO session_runs
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-1",
                "session-1",
                "thread-1",
                None,
                "running",
                '{"tenant":"migration","project":"alpha"}',
                None,
                1,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "{}",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        await connection.execute(
            """
            INSERT INTO session_events
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-1",
                "session-1",
                "run-1",
                None,
                1,
                "run.started",
                "builder",
                "{}",
                '{"tenant":"migration","project":"alpha"}',
                None,
                None,
                "2026-01-01T00:00:00",
            ),
        )
        await connection.execute(
            """
            INSERT INTO artifacts
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "artifact-1",
                "artifact",
                "file",
                '{"tenant":"migration","project":"alpha"}',
                1,
                b"data",
                "{}",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        await connection.execute(
            """
            INSERT INTO config_entities
            (entity_type, name, scope, definition, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "agent",
                "migration-agent",
                '{"tenant":"migration","project":"alpha"}',
                (
                    '{"name":"migration-agent","mode":"primary",'
                    '"system_prompt":"Migrated agent."}'
                ),
                "api",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        await connection.execute(
            """
            INSERT INTO config_changes
            (entity_type, name, scope, operation, changed_at, processed)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "agent",
                "migration-agent",
                '{"tenant":"migration","project":"alpha"}',
                "upsert",
                "2026-01-01T00:00:00",
                False,
            ),
        )
        await connection.commit()

    returncode, output = await _run_alembic(database, "upgrade", "head")
    assert returncode == 0, output

    expected_scope_key = effective_scope_key(scope)
    expected_definition_digest = canonical_json_digest(definition)
    async with aiosqlite.connect(database) as connection:
        async with connection.execute("SELECT version_num FROM alembic_version") as cursor:
            version = await cursor.fetchone()
        async with connection.execute("SELECT scope_key FROM sessions") as cursor:
            session_scope_key = await cursor.fetchone()
        async with connection.execute(
            """
            SELECT scope_key, agent_revision, runtime_manifest, manifest_digest
            FROM session_runs
            """
        ) as cursor:
            run_row = await cursor.fetchone()
        async with connection.execute("SELECT scope_key FROM session_events") as cursor:
            event_scope_key = await cursor.fetchone()
        async with connection.execute("SELECT scope_key FROM artifacts") as cursor:
            artifact_scope_key = await cursor.fetchone()
        async with connection.execute(
            """
            SELECT scope_key, revision, definition_digest
            FROM config_entities
            """
        ) as cursor:
            config_row = await cursor.fetchone()

    assert version == ("007",)
    assert session_scope_key == (expected_scope_key,)
    assert run_row == (
        expected_scope_key,
        1,
        "{}",
        canonical_json_digest({}),
    )
    assert event_scope_key == (expected_scope_key,)
    assert artifact_scope_key == (expected_scope_key,)
    assert config_row == (expected_scope_key, 1, expected_definition_digest)


async def test_v014_migration_normalizes_persisted_agent_definitions(
    tmp_path: Path,
) -> None:
    """Revision 007 makes RC4 and v0.13 Agent rows readable by v0.14."""
    database = tmp_path / "legacy-v014.db"
    returncode, output = await _run_alembic(database, "upgrade", "006")
    assert returncode == 0, output

    alice_scope = {"tenant": "acme", "project": "alpha"}
    bob_scope = {"tenant": "acme", "project": "beta"}
    public_skill = {
        "id": "analysis",
        "name": "Analysis",
        "description": "Analyze documents.",
        "tags": ["documents"],
    }
    rc4_definition = {
        "name": "rc4-agent",
        "system_prompt": "RC4 agent.",
        "mode": "primary",
        "skills": [
            {
                "name": "legacy-bundle",
                "content": "---\nname: legacy-bundle\n---\nLegacy instructions.",
                "files": {"reference.md": "Legacy reference."},
            }
        ],
        "subagents": [
            {
                "name": "researcher",
                "system_prompt": "Research.",
                "tools": ["legacy-tool"],
            }
        ],
        "a2a": {"exposed": True, "skills": [public_skill]},
    }
    v013_definition = {
        "name": "v013-agent",
        "system_prompt": "v0.13 agent.",
        "skills": ["registry-skill"],
        "tools": ["registry-tool"],
    }
    unchanged_definition = {
        "name": "current-agent",
        "system_prompt": "Current agent.",
    }
    provider_definition = {
        "id": "provider",
        "skills": ["not-an-agent-field"],
    }
    historical_manifest = {
        "agent": {
            "name": "rc4-agent",
            "revision": 4,
            "definition": rc4_definition,
        }
    }

    async with aiosqlite.connect(database) as connection:
        for entity_type, name, scope, definition, revision in (
            ("agent", "rc4-agent", alice_scope, rc4_definition, 4),
            ("agent", "v013-agent", bob_scope, v013_definition, 2),
            ("agent", "current-agent", alice_scope, unchanged_definition, 3),
            ("provider", "provider", alice_scope, provider_definition, 7),
        ):
            await connection.execute(
                """
                INSERT INTO config_entities
                (entity_type, name, scope, scope_key, definition, revision,
                 definition_digest, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'api', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    entity_type,
                    name,
                    json.dumps(scope),
                    effective_scope_key(scope),
                    json.dumps(definition),
                    revision,
                    canonical_json_digest(definition),
                ),
            )
        await connection.execute(
            """
            INSERT INTO session_runs
            (id, session_id, thread_id, status, effective_scope, scope_key,
             agent_revision, runtime_manifest, manifest_digest, attempt, metadata,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                "run-1",
                "session-1",
                "thread-1",
                "completed",
                json.dumps(alice_scope),
                effective_scope_key(alice_scope),
                4,
                json.dumps(historical_manifest),
                canonical_json_digest(historical_manifest),
                1,
                "{}",
            ),
        )
        await connection.commit()

    returncode, output = await _run_alembic(database, "upgrade", "head")
    assert returncode == 0, output

    expected_rc4 = json.loads(json.dumps(rc4_definition))
    expected_rc4.pop("skills")
    expected_rc4.pop("tools", None)
    expected_rc4["subagents"][0].pop("tools")
    expected_v013 = {
        key: value
        for key, value in v013_definition.items()
        if key not in {"skills", "tools"}
    }
    async with aiosqlite.connect(database) as connection:
        connection.row_factory = aiosqlite.Row
        rows = {
            row["name"]: row
            async for row in await connection.execute(
                """
                SELECT name, definition, revision, definition_digest
                FROM config_entities
                ORDER BY name
                """
            )
        }
        run_row = await (
            await connection.execute(
                """
                SELECT agent_revision, runtime_manifest, manifest_digest
                FROM session_runs WHERE id='run-1'
                """
            )
        ).fetchone()
        version = await (
            await connection.execute("SELECT version_num FROM alembic_version")
        ).fetchone()

    assert version is not None
    assert version[0] == "007"
    assert json.loads(rows["rc4-agent"]["definition"]) == expected_rc4
    assert rows["rc4-agent"]["revision"] == 5
    assert rows["rc4-agent"]["definition_digest"] == canonical_json_digest(expected_rc4)
    assert json.loads(rows["v013-agent"]["definition"]) == expected_v013
    assert rows["v013-agent"]["revision"] == 3
    assert rows["v013-agent"]["definition_digest"] == canonical_json_digest(expected_v013)
    assert json.loads(rows["current-agent"]["definition"]) == unchanged_definition
    assert rows["current-agent"]["revision"] == 3
    assert rows["current-agent"]["definition_digest"] == canonical_json_digest(
        unchanged_definition
    )
    assert json.loads(rows["provider"]["definition"]) == provider_definition
    assert rows["provider"]["revision"] == 7
    assert run_row is not None
    assert run_row["agent_revision"] == 4
    assert json.loads(run_row["runtime_manifest"]) == historical_manifest
    assert run_row["manifest_digest"] == canonical_json_digest(historical_manifest)

    registry = SqliteConfigRegistry(str(database))
    store = DefaultConfigStore(registry, workspace_path=tmp_path)
    try:
        exact_agent = await store.get_agent_definition("rc4-agent", alice_scope)
        assert exact_agent is not None
        assert exact_agent.a2a.skills[0].id == "analysis"
        assert await store.get_agent_definition("rc4-agent", bob_scope) is None
        assert {agent.name for agent in await store.list_agent_definitions(scope=alice_scope)} == {
            "current-agent",
            "rc4-agent",
        }
        card = build_agent_card_for_agent(exact_agent, "https://agents.example", "0.14.1")
        assert [skill.id for skill in card.skills] == ["analysis"]
    finally:
        await registry.close()

    before_rerun = {
        name: (row["definition"], row["revision"], row["definition_digest"])
        for name, row in rows.items()
    }
    returncode, output = await _run_alembic(database, "downgrade", "006")
    assert returncode == 0, output
    returncode, output = await _run_alembic(database, "upgrade", "head")
    assert returncode == 0, output
    async with aiosqlite.connect(database) as connection:
        connection.row_factory = aiosqlite.Row
        rerun_rows = {
            row["name"]: (
                row["definition"],
                row["revision"],
                row["definition_digest"],
            )
            async for row in await connection.execute(
                """
                SELECT name, definition, revision, definition_digest
                FROM config_entities ORDER BY name
                """
            )
        }
    assert rerun_rows == before_rerun
