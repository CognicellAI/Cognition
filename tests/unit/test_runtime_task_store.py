"""Boundary tests for protocol-neutral durable runtime tasks."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from server.app.models import RunStatus, SessionConfig, TaskStatus
from server.app.storage.backend import StorageBackend
from server.app.storage.memory import MemoryStorageBackend
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture(params=["memory", "sqlite"])
async def task_store(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[StorageBackend]:
    """Provide both local-substitutable storage implementations."""
    store: StorageBackend
    if request.param == "memory":
        store = MemoryStorageBackend(workspace_path=str(tmp_path))
    else:
        store = SqliteStorageBackend(
            connection_string=str(tmp_path / "tasks.db"),
            workspace_path=str(tmp_path),
        )
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def _session(store: StorageBackend, session_id: str, scope: dict[str, str]) -> None:
    await store.create_session(
        session_id=session_id,
        thread_id=str(uuid.uuid4()),
        config=SessionConfig(),
        scopes=scope,
        agent_name="analyst",
    )


@pytest.mark.asyncio
async def test_task_scope_agent_and_terminal_transition_are_enforced(
    task_store: StorageBackend,
) -> None:
    scope = {"project": "kennel", "end_user": "alice"}
    await _session(task_store, "context-1", scope)
    task = await task_store.create_task(
        task_id="task-1",
        context_id="context-1",
        session_id="context-1",
        agent_name="analyst",
        effective_scope=scope,
        idempotency_key="message-1",
    )

    assert await task_store.get_task("task-1", scope, "analyst") == task
    assert await task_store.get_task("task-1", {"project": "other"}, "analyst") is None
    assert await task_store.get_task("task-1", scope, "other-agent") is None

    working = await task_store.update_task(
        "task-1",
        scope,
        expected_statuses={TaskStatus.SUBMITTED},
        status=TaskStatus.WORKING,
    )
    assert working is not None and working.status == TaskStatus.WORKING
    completed = await task_store.update_task(
        "task-1",
        scope,
        expected_statuses={TaskStatus.WORKING},
        status=TaskStatus.COMPLETED,
    )
    assert completed is not None and completed.status == TaskStatus.COMPLETED
    assert (
        await task_store.update_task(
            "task-1",
            scope,
            expected_statuses={TaskStatus.COMPLETED},
            status=TaskStatus.CANCELED,
        )
        is None
    )


@pytest.mark.asyncio
async def test_task_idempotency_and_cursor_pagination_are_scope_namespaced(
    task_store: StorageBackend,
) -> None:
    scope = {"project": "kennel"}
    other_scope = {"project": "other"}
    await _session(task_store, "context-1", scope)
    await _session(task_store, "context-2", other_scope)
    for index in range(3):
        await task_store.create_task(
            task_id=f"task-{index}",
            context_id="context-1",
            session_id="context-1",
            agent_name="analyst",
            effective_scope=scope,
            idempotency_key=f"message-{index}",
        )
    await task_store.create_task(
        task_id="other-task",
        context_id="context-2",
        session_id="context-2",
        agent_name="analyst",
        effective_scope=other_scope,
        idempotency_key="message-0",
    )

    existing = await task_store.get_task_by_idempotency_key("analyst", scope, "message-0")
    assert existing is not None and existing.id == "task-0"
    page_one, cursor = await task_store.list_tasks("analyst", scope, limit=2)
    page_two, final_cursor = await task_store.list_tasks("analyst", scope, limit=2, cursor=cursor)
    assert len(page_one) == 2
    assert len(page_two) == 1
    assert cursor is not None
    assert final_cursor is None
    assert {task.id for task in page_one + page_two} == {"task-0", "task-1", "task-2"}


@pytest.mark.asyncio
async def test_run_and_events_retain_task_correlation(task_store: StorageBackend) -> None:
    scope = {"project": "kennel"}
    await _session(task_store, "context-1", scope)
    await task_store.create_task(
        task_id="task-1",
        context_id="context-1",
        session_id="context-1",
        agent_name="analyst",
        effective_scope=scope,
    )
    run = await task_store.create_run(
        run_id="run-1",
        session_id="context-1",
        thread_id="thread-1",
        status=RunStatus.ACTIVE,
        effective_scope=scope,
        task_id="task-1",
    )
    event = await task_store.append_event(
        event_id="event-1",
        session_id="context-1",
        run_id=run.id,
        event_type="run.started",
        effective_scope=scope,
    )

    assert run.task_id == "task-1"
    assert event.task_id == "task-1"
    events = await task_store.list_events("context-1", task_id="task-1")
    assert [item.id for item in events] == ["event-1"]
