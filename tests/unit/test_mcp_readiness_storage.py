"""Tests for durable, exact-scope MCP readiness observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from server.app.storage.mcp_readiness import (
    McpReadinessObservation,
    SqlMcpReadinessRepository,
)


@pytest.mark.asyncio
async def test_sqlite_readiness_survives_repository_restart(tmp_path) -> None:
    database = tmp_path / "readiness.db"
    url = f"sqlite+aiosqlite:///{database}"
    first = SqlMcpReadinessRepository(create_async_engine(url))
    await first.initialize()
    now = datetime.now(UTC)
    await first.record(
        McpReadinessObservation(
            agent_name="support",
            agent_revision=4,
            server_alias="github",
            required=True,
            status="ready",
            tool_count=3,
            schema_digest="b" * 64,
            observed_at=now,
            fresh_until=now + timedelta(minutes=5),
        ),
        {"tenant": "alpha"},
    )
    await first.close()

    second = SqlMcpReadinessRepository(create_async_engine(url))
    await second.initialize()
    matching = await second.list_for_agent(
        agent_name="support",
        agent_revision=4,
        effective_scope={"tenant": "alpha"},
    )
    other_scope = await second.list_for_agent(
        agent_name="support",
        agent_revision=4,
        effective_scope={"tenant": "beta"},
    )
    await second.close()

    assert len(matching) == 1
    assert matching[0].tool_count == 3
    assert matching[0].schema_digest == "b" * 64
    assert other_scope == []
