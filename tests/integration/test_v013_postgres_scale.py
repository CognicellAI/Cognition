"""Reduced PostgreSQL scale fixture for v0.13 multi-tenant indexes."""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from server.app.storage.common import canonical_json_digest, effective_scope_key

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.slow]


def _postgres_dsn() -> str:
    dsn = os.environ.get("COGNITION_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set COGNITION_TEST_POSTGRES_DSN to run PostgreSQL scale validation")
    return dsn


def _plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan]
    for child in plan.get("Plans") or []:
        if isinstance(child, dict):
            nodes.extend(_plan_nodes(child))
    return nodes


def _assert_indexed_plan(
    plan_result: Any,
    *,
    expected_index: str,
    max_scan_rows: int,
) -> None:
    payload = plan_result
    if isinstance(payload, str):
        payload = json.loads(payload)
    plan = payload[0]["Plan"]
    nodes = _plan_nodes(plan)
    node_types = {str(node.get("Node Type")) for node in nodes}
    index_names = {str(node.get("Index Name")) for node in nodes if node.get("Index Name")}
    assert "Seq Scan" not in node_types
    assert expected_index in index_names
    scan_rows = [
        int(node.get("Actual Rows") or 0)
        for node in nodes
        if "Scan" in str(node.get("Node Type"))
    ]
    assert scan_rows
    assert max(scan_rows) <= max_scan_rows


@pytest.mark.asyncio
async def test_ci_sized_postgres_scope_indexes_are_used(
    record_property: Any,
) -> None:
    asyncpg = pytest.importorskip("asyncpg")
    dsn = _postgres_dsn()
    schema = f"cognition_v013_scale_{uuid.uuid4().hex}"
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute(
            """
            CREATE TABLE config_entities (
                id BIGSERIAL PRIMARY KEY,
                entity_type VARCHAR(50) NOT NULL,
                name VARCHAR(200) NOT NULL,
                scope JSONB NOT NULL,
                scope_key VARCHAR(64) NOT NULL,
                definition JSONB NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                definition_digest VARCHAR(64) NOT NULL,
                source VARCHAR(10) NOT NULL DEFAULT 'api',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX idx_config_entities_lookup
            ON config_entities (entity_type, name, scope_key)
            """
        )
        await conn.execute(
            """
            CREATE INDEX idx_config_entities_scope_list
            ON config_entities (entity_type, scope_key, name)
            """
        )
        await conn.execute(
            """
            CREATE TABLE sessions (
                id VARCHAR(36) PRIMARY KEY,
                workspace_path TEXT NOT NULL,
                title TEXT,
                thread_id VARCHAR(36) NOT NULL,
                status VARCHAR(20) NOT NULL,
                config JSONB NOT NULL,
                scopes JSONB NOT NULL,
                scope_key VARCHAR(64) NOT NULL,
                metadata JSONB NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                agent_name VARCHAR(100) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX idx_sessions_scope_page
            ON sessions (scope_key, updated_at, id)
            """
        )

        scopes = [{"tenant": f"tenant-{i:04d}"} for i in range(1000)]
        scope_keys = [effective_scope_key(scope) for scope in scopes]
        config_rows = []
        for i in range(10_000):
            scope = scopes[i % len(scopes)]
            definition = {
                "name": f"agent-{i:05d}",
                "mode": "primary",
                "system_prompt": f"Agent {i}",
            }
            config_rows.append(
                (
                    "agent",
                    definition["name"],
                    json.dumps(scope),
                    scope_keys[i % len(scopes)],
                    json.dumps(definition),
                    1,
                    canonical_json_digest(definition),
                    "api",
                )
            )
        await conn.copy_records_to_table(
            "config_entities",
            records=config_rows,
            columns=[
                "entity_type",
                "name",
                "scope",
                "scope_key",
                "definition",
                "revision",
                "definition_digest",
                "source",
            ],
        )

        session_rows = []
        for i in range(50_000):
            scope = scopes[i % len(scopes)]
            session_rows.append(
                (
                    str(uuid.uuid4()),
                    "/workspace",
                    f"Session {i}",
                    str(uuid.uuid4()),
                    "active",
                    "{}",
                    json.dumps(scope),
                    scope_keys[i % len(scopes)],
                    "{}",
                    0,
                    f"agent-{i % 10_000:05d}",
                    datetime(2026, 1, 1, 0, (i // 60) % 60, i % 60, tzinfo=UTC),
                    datetime(2026, 1, 2, 0, (i // 60) % 60, i % 60, tzinfo=UTC),
                )
            )
        await conn.copy_records_to_table(
            "sessions",
            records=session_rows,
            columns=[
                "id",
                "workspace_path",
                "title",
                "thread_id",
                "status",
                "config",
                "scopes",
                "scope_key",
                "metadata",
                "message_count",
                "agent_name",
                "created_at",
                "updated_at",
            ],
        )
        await conn.execute("ANALYZE config_entities")
        await conn.execute("ANALYZE sessions")

        exact_scope = scopes[777]
        exact_scope_key = scope_keys[777]
        exact_agent = "agent-07777"
        exact_scope_json = json.dumps(exact_scope, sort_keys=True)

        exact_plan = await conn.fetchval(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT definition, revision, definition_digest
            FROM config_entities
            WHERE entity_type='agent'
              AND name=$1
              AND scope_key=$2
              AND scope=$3::jsonb
            """,
            exact_agent,
            exact_scope_key,
            exact_scope_json,
        )
        _assert_indexed_plan(
            exact_plan,
            expected_index="idx_config_entities_lookup",
            max_scan_rows=10,
        )

        list_plan = await conn.fetchval(
            """
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT *
            FROM sessions
            WHERE scope_key=$1
            ORDER BY updated_at DESC, id DESC
            LIMIT 100 OFFSET 0
            """,
            exact_scope_key,
        )
        _assert_indexed_plan(
            list_plan,
            expected_index="idx_sessions_scope_page",
            max_scan_rows=250,
        )

        exact_timings: list[float] = []
        list_timings: list[float] = []
        for _ in range(15):
            started = time.perf_counter()
            await conn.fetchrow(
                """
                SELECT definition, revision, definition_digest
                FROM config_entities
                WHERE entity_type='agent'
                  AND name=$1
                  AND scope_key=$2
                  AND scope=$3::jsonb
                """,
                exact_agent,
                exact_scope_key,
                exact_scope_json,
            )
            exact_timings.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            await conn.fetch(
                """
                SELECT *
                FROM sessions
                WHERE scope_key=$1
                ORDER BY updated_at DESC, id DESC
                LIMIT 100 OFFSET 0
                """,
                exact_scope_key,
            )
            list_timings.append((time.perf_counter() - started) * 1000)

        record_property("postgres_exact_lookup_median_ms", statistics.median(exact_timings))
        record_property("postgres_exact_lookup_p95_ms", sorted(exact_timings)[-1])
        record_property("postgres_session_page_median_ms", statistics.median(list_timings))
        record_property("postgres_session_page_p95_ms", sorted(list_timings)[-1])
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await conn.close()
