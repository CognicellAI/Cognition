"""Tests for the shared protocol-neutral agent task lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.app.agent.task_runtime import (
    AgentTaskRuntime,
    CancelTask,
    ContinueTask,
    GetTask,
    ListTasks,
    SubmitTask,
    SubscribeTask,
)
from server.app.exceptions import (
    RuntimeTaskNotCancelableError,
    RuntimeTaskNotFoundError,
)
from server.app.models import RunStatus, TaskStatus
from server.app.storage.backend import StorageBackend
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture
def task_runtime(setup_storage_backend: StorageBackend, tmp_path) -> AgentTaskRuntime:
    """Build the lifecycle service over the test's durable SQLite backend."""
    return AgentTaskRuntime(
        setup_storage_backend,
        default_workspace_path=str(tmp_path),
    )


async def test_submit_is_idempotent_and_correlates_task_run_and_message(
    task_runtime: AgentTaskRuntime,
) -> None:
    command = SubmitTask(
        agent_name="researcher",
        effective_scope={"account": "acme"},
        content="Analyze the filing",
        idempotency_key="message-1",
        metadata={"source": "a2a-jsonrpc", "external_message_id": "message-1"},
    )

    first = await task_runtime.submit(command)
    second = await task_runtime.submit(command)

    assert second.reused is True
    assert second.task.id == first.task.id
    assert second.run.id == first.run.id
    assert first.task.status == TaskStatus.WORKING
    assert first.run.task_id == first.task.id
    assert first.user_message.metadata == {
        "source": "a2a-jsonrpc",
        "external_message_id": "message-1",
        "task_id": first.task.id,
        "run_id": first.run.id,
    }


async def test_context_and_task_are_hidden_across_scope_and_agent(
    task_runtime: AgentTaskRuntime,
) -> None:
    execution = await task_runtime.submit(
        SubmitTask(
            agent_name="researcher",
            effective_scope={"account": "acme"},
            content="Analyze",
        )
    )

    assert (
        await task_runtime.get(GetTask(execution.task.id, "researcher", {"account": "other"}))
        is None
    )
    assert (
        await task_runtime.get(GetTask(execution.task.id, "other-agent", {"account": "acme"}))
        is None
    )
    with pytest.raises(RuntimeTaskNotFoundError):
        await task_runtime.submit(
            SubmitTask(
                agent_name="researcher",
                effective_scope={"account": "other"},
                context_id=execution.task.context_id,
                content="Cross scope",
            )
        )


async def test_interrupted_task_continues_with_new_run_and_same_task(
    task_runtime: AgentTaskRuntime,
) -> None:
    first = await task_runtime.submit(
        SubmitTask(
            agent_name="researcher",
            effective_scope={},
            content="Use a protected tool",
        )
    )
    paused_task, paused_run, _ = await task_runtime.transition(
        first.task,
        first.run,
        RunStatus.INTERRUPTED,
        reason="Approval required",
    )
    assert paused_task.status == TaskStatus.INPUT_REQUIRED

    continued = await task_runtime.continue_task(
        ContinueTask(
            task_id=first.task.id,
            agent_name="researcher",
            effective_scope={},
            content="Approved",
            idempotency_key="approval-1",
        )
    )

    assert continued.task.id == first.task.id
    assert continued.run.id != paused_run.id
    assert continued.run.attempt == paused_run.attempt + 1
    assert continued.run.parent_run_id == paused_run.id
    assert continued.task.status == TaskStatus.WORKING


async def test_cancel_is_exact_terminal_and_replayable(
    task_runtime: AgentTaskRuntime,
) -> None:
    execution = await task_runtime.submit(
        SubmitTask(
            agent_name="researcher",
            effective_scope={"project": "alpha"},
            content="Long task",
        )
    )
    abort_calls: list[tuple[str, str]] = []

    async def abort(session_id: str, thread_id: str) -> bool:
        abort_calls.append((session_id, thread_id))
        return True

    canceled = await task_runtime.cancel(
        CancelTask(
            execution.task.id,
            execution.task.agent_name,
            execution.task.effective_scope,
        ),
        abort_execution=abort,
    )
    events = [
        event
        async for event in task_runtime.subscribe(
            SubscribeTask(
                canceled.id,
                canceled.agent_name,
                canceled.effective_scope,
                poll_interval=0.01,
            )
        )
    ]

    assert canceled.status == TaskStatus.CANCELED
    assert abort_calls == [(execution.run.session_id, execution.run.thread_id)]
    assert events[-1].event_type == "run.aborted"
    with pytest.raises(RuntimeTaskNotCancelableError):
        await task_runtime.cancel(
            CancelTask(canceled.id, canceled.agent_name, canceled.effective_scope)
        )


async def test_completion_cancellation_race_has_one_persisted_terminal_winner(
    task_runtime: AgentTaskRuntime,
) -> None:
    execution = await task_runtime.submit(
        SubmitTask(
            agent_name="researcher",
            effective_scope={"project": "race"},
            content="Race completion and cancellation",
        )
    )

    outcomes = await asyncio.gather(
        task_runtime.transition(execution.task, execution.run, RunStatus.DONE),
        task_runtime.cancel(
            CancelTask(
                execution.task.id,
                execution.task.agent_name,
                execution.task.effective_scope,
            )
        ),
        return_exceptions=True,
    )
    winner = await task_runtime.get(
        GetTask(
            execution.task.id,
            execution.task.agent_name,
            execution.task.effective_scope,
        )
    )

    assert winner is not None
    assert winner.status in {TaskStatus.COMPLETED, TaskStatus.CANCELED}
    assert not TaskStatus.can_transition(winner.status, TaskStatus.FAILED)
    if winner.status == TaskStatus.COMPLETED:
        assert any(isinstance(outcome, RuntimeTaskNotCancelableError) for outcome in outcomes)


async def test_list_is_agent_and_scope_bounded(task_runtime: AgentTaskRuntime) -> None:
    execution = await task_runtime.submit(
        SubmitTask(
            agent_name="researcher",
            effective_scope={"account": "acme"},
            content="One",
        )
    )

    page = await task_runtime.list(
        ListTasks("researcher", {"account": "acme"}, context_id=execution.task.context_id)
    )
    other_scope = await task_runtime.list(ListTasks("researcher", {"account": "other"}))

    assert [task.id for task in page.tasks] == [execution.task.id]
    assert other_scope.tasks == []


async def test_task_survives_restart_and_replays_from_a_new_runtime_replica(
    tmp_path: Path,
) -> None:
    """A second runtime instance can retrieve, subscribe, and cancel durable work."""
    database = tmp_path / "replica.db"
    scope = {"project": "kennel", "end_user": "alice"}
    first_store = SqliteStorageBackend(str(database), str(tmp_path))
    await first_store.initialize()
    first_runtime = AgentTaskRuntime(
        first_store,
        default_workspace_path=str(tmp_path),
    )
    execution = await first_runtime.submit(
        SubmitTask(
            agent_name="analyst",
            effective_scope=scope,
            content="Wait for approval",
        )
    )
    await first_runtime.transition(
        execution.task,
        execution.run,
        RunStatus.INTERRUPTED,
        reason="Approval required",
    )
    await first_store.close()

    second_store = SqliteStorageBackend(str(database), str(tmp_path))
    await second_store.initialize()
    try:
        second_runtime = AgentTaskRuntime(
            second_store,
            default_workspace_path=str(tmp_path),
        )
        restored = await second_runtime.get(GetTask(execution.task.id, "analyst", scope))
        assert restored is not None
        assert restored.status == TaskStatus.INPUT_REQUIRED

        replay = second_runtime.subscribe(
            SubscribeTask(execution.task.id, "analyst", scope, poll_interval=0.01)
        )
        replayed_event = await anext(replay)
        assert replayed_event.task_id == execution.task.id
        assert replayed_event.event_type == "run.started"

        canceled = await second_runtime.cancel(CancelTask(execution.task.id, "analyst", scope))
        remaining_events = [event async for event in replay]
        assert canceled.status == TaskStatus.CANCELED
        assert remaining_events[-1].event_type == "task.canceled"
    finally:
        await second_store.close()
