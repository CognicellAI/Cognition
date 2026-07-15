"""Clean-database migration checks for the server schema."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import aiosqlite


async def test_alembic_upgrade_head_creates_runtime_task_schema(tmp_path: Path) -> None:
    """A base installation can migrate a fresh SQLite database to head."""
    database = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["COGNITION_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "server/alembic.ini",
        "upgrade",
        "head",
        cwd=Path(__file__).parents[2],
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()

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

    assert version == ("004",)
    assert "runtime_tasks" in tables
    assert "task_id" in run_columns
    assert "task_id" in event_columns
