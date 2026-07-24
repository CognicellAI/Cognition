"""Adversarial exact-scope persistence tests for the v0.13 runtime boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator

import pytest

from server.app.models import RunStatus, SessionConfig, TaskStatus
from server.app.storage.artifact_store import (
    MemoryArtifactStore,
    SqliteArtifactStore,
)
from server.app.storage.backend import StorageBackend
from server.app.storage.common import effective_scope_key
from server.app.storage.config_models import ArtifactDefinition
from server.app.storage.memory import MemoryStorageBackend
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture(params=["memory", "sqlite"])
async def isolated_backend(
    request: pytest.FixtureRequest,
    tmp_path,
) -> AsyncIterator[StorageBackend]:
    if request.param == "memory":
        backend: StorageBackend = MemoryStorageBackend(str(tmp_path))
    else:
        backend = SqliteStorageBackend(
            connection_string=str(tmp_path / "v013-isolation.db"),
            workspace_path=str(tmp_path),
        )
    await backend.initialize()
    yield backend
    await backend.close()


@pytest.mark.asyncio
async def test_session_message_run_event_and_task_access_fail_closed(
    isolated_backend: StorageBackend,
) -> None:
    owner = {"tenant": "acme", "project": "red"}
    sibling = {"tenant": "acme", "project": "blue"}
    partial = {"tenant": "acme"}
    empty: dict[str, str] = {}

    session = await isolated_backend.create_session(
        session_id="scope-session",
        thread_id="scope-thread",
        config=SessionConfig(),
        agent_name="builder-agent",
        scopes=owner,
    )
    assert session.scopes == owner

    for wrong_scope in (sibling, partial, empty):
        assert (
            await isolated_backend.get_session(session.id, wrong_scope)
            is None
        )
        assert await isolated_backend.list_sessions(wrong_scope) == []
        assert (
            await isolated_backend.update_session(
                session.id,
                title="must-not-change",
                effective_scope=wrong_scope,
            )
            is None
        )
        assert not await isolated_backend.delete_session(session.id, wrong_scope)

    message = await isolated_backend.create_message(
        message_id="scope-message",
        session_id=session.id,
        role="user",
        content="owner data",
        effective_scope=owner,
    )
    for wrong_scope in (sibling, partial, empty):
        assert (
            await isolated_backend.get_message(message.id, wrong_scope)
            is None
        )
        assert (
            await isolated_backend.get_messages_by_session(
                session.id,
                effective_scope=wrong_scope,
            )
            == ([], 0)
        )
        assert (
            await isolated_backend.delete_messages_for_session(
                session.id,
                wrong_scope,
            )
            == 0
        )
        with pytest.raises(ValueError, match="exact message scope"):
            await isolated_backend.create_message(
                message_id=f"wrong-{len(wrong_scope)}",
                session_id=session.id,
                role="user",
                content="must fail",
                effective_scope=wrong_scope,
            )

    task = await isolated_backend.create_task(
        task_id="scope-task",
        context_id=session.id,
        session_id=session.id,
        agent_name="builder-agent",
        effective_scope=owner,
    )
    run = await isolated_backend.create_run(
        run_id="scope-run",
        session_id=session.id,
        thread_id=session.thread_id,
        effective_scope=owner,
        task_id=task.id,
        agent_revision=3,
        runtime_manifest={"agent": {"name": "builder-agent", "revision": 3}},
    )
    event = await isolated_backend.append_event(
        event_id="scope-event",
        session_id=session.id,
        run_id=run.id,
        event_type="status",
        effective_scope=owner,
        task_id=task.id,
    )

    for wrong_scope in (sibling, partial, empty):
        assert await isolated_backend.get_task(task.id, wrong_scope) is None
        assert (
            await isolated_backend.update_task(
                task.id,
                wrong_scope,
                status=TaskStatus.WORKING,
            )
            is None
        )
        tasks, cursor = await isolated_backend.list_tasks(
            "builder-agent",
            wrong_scope,
        )
        assert tasks == []
        assert cursor is None
        assert await isolated_backend.get_run(run.id, wrong_scope) is None
        assert await isolated_backend.list_runs(session.id, wrong_scope) == []
        assert (
            await isolated_backend.update_run(
                run.id,
                status=RunStatus.ACTIVE,
                effective_scope=wrong_scope,
            )
            is None
        )
        assert (
            await isolated_backend.list_events(
                session.id,
                run_id=run.id,
                effective_scope=wrong_scope,
            )
            == []
        )
        assert not await isolated_backend.delete_task_data(task.id, wrong_scope)
        with pytest.raises(ValueError, match="exact event scope"):
            await isolated_backend.append_event(
                event_id=f"wrong-event-{len(wrong_scope)}",
                session_id=session.id,
                run_id=run.id,
                event_type="status",
                effective_scope=wrong_scope,
            )

    assert (await isolated_backend.get_message(message.id, owner)) is not None
    assert (await isolated_backend.get_run(run.id, owner)) is not None
    assert (
        await isolated_backend.list_events(
            session.id,
            run_id=run.id,
            effective_scope=owner,
        )
    ) == [event]


@pytest.mark.asyncio
async def test_terminal_cleanup_requires_exact_scope(
    isolated_backend: StorageBackend,
) -> None:
    owner = {"tenant": "cleanup-owner"}
    wrong = {"tenant": "cleanup-other"}
    await isolated_backend.create_session(
        session_id="cleanup-session",
        thread_id="cleanup-thread",
        config=SessionConfig(),
        agent_name="cleanup-agent",
        scopes=owner,
    )
    task = await isolated_backend.create_task(
        task_id="cleanup-task",
        context_id="cleanup-session",
        session_id="cleanup-session",
        agent_name="cleanup-agent",
        effective_scope=owner,
        status=TaskStatus.COMPLETED,
    )
    await isolated_backend.create_run(
        run_id="cleanup-run",
        session_id="cleanup-session",
        thread_id="cleanup-thread",
        status=RunStatus.DONE,
        effective_scope=owner,
        task_id=task.id,
    )
    await isolated_backend.append_event(
        event_id="cleanup-event",
        session_id="cleanup-session",
        run_id="cleanup-run",
        event_type="done",
        effective_scope=owner,
        task_id=task.id,
    )

    assert not await isolated_backend.delete_task_data(task.id, wrong)
    assert await isolated_backend.get_task(task.id, owner) is not None
    assert await isolated_backend.delete_task_data(task.id, owner)
    assert await isolated_backend.get_task(task.id, owner) is None
    assert await isolated_backend.get_run("cleanup-run", owner) is None
    assert (
        await isolated_backend.list_events(
            "cleanup-session",
            effective_scope=owner,
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
async def test_artifact_identity_includes_exact_scope(
    store_kind: str,
    tmp_path,
) -> None:
    owner = {"tenant": "artifact-owner"}
    sibling = {"tenant": "artifact-sibling"}
    unrelated = {"tenant": "artifact-unrelated"}
    if store_kind == "memory":
        store: MemoryArtifactStore | SqliteArtifactStore = MemoryArtifactStore()
    else:
        backend = SqliteStorageBackend(
            str(tmp_path / "artifacts.db"),
            str(tmp_path),
        )
        await backend.initialize()
        await backend.close()
        store = SqliteArtifactStore(str(tmp_path / "artifacts.db"))
    await store.initialize()
    try:
        await store.upsert_artifact(
            ArtifactDefinition(
                id="shared-id",
                name="owner-artifact",
                content="owner",
                scope=owner,
            )
        )
        await store.upsert_artifact(
            ArtifactDefinition(
                id="shared-id",
                name="sibling-artifact",
                content="sibling",
                scope=sibling,
            )
        )

        assert (await store.get_artifact("shared-id", owner)).content == "owner"  # type: ignore[union-attr]
        assert (await store.get_artifact("shared-id", sibling)).content == "sibling"  # type: ignore[union-attr]
        assert await store.get_artifact("shared-id", unrelated) is None
        assert await store.list_artifacts(unrelated) == []
        assert not await store.delete_artifact("shared-id", unrelated)
        assert await store.delete_artifact("shared-id", owner)
        assert await store.get_artifact("shared-id", owner) is None
        assert (await store.get_artifact("shared-id", sibling)) is not None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_scoped_session_page_uses_composite_index(tmp_path) -> None:
    backend = SqliteStorageBackend(
        str(tmp_path / "indexed.db"),
        str(tmp_path),
    )
    await backend.initialize()
    scope = {"tenant": "index-tenant", "project": "index-project"}
    try:
        for index in range(130):
            await backend.create_session(
                session_id=f"indexed-{index:03d}",
                thread_id=f"thread-{index:03d}",
                config=SessionConfig(),
                agent_name="indexed-agent",
                scopes=scope,
            )

        first_page = await backend.list_sessions(scope, limit=100)
        second_page = await backend.list_sessions(scope, limit=100, offset=100)
        assert len(first_page) == 100
        assert len(second_page) == 30
        assert {session.id for session in first_page}.isdisjoint(
            session.id for session in second_page
        )

        with sqlite3.connect(backend.db_path) as connection:
            plan = connection.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT * FROM sessions
                WHERE scope_key = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 100
                """,
                (effective_scope_key(scope),),
            ).fetchall()
        plan_text = " ".join(str(row) for row in plan)
        assert "idx_sessions_scope_page" in plan_text
        assert "SCAN sessions" not in plan_text
    finally:
        await backend.close()
