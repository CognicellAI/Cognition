"""Protocol-neutral durable task lifecycle for agent execution."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from server.app.exceptions import (
    RuntimeTaskConflictError,
    RuntimeTaskNotCancelableError,
    RuntimeTaskNotFoundError,
)
from server.app.models import (
    Message,
    RunStatus,
    RuntimeTask,
    Session,
    SessionConfig,
    SessionEvent,
    SessionRun,
    TaskStatus,
    ToolCall,
)
from server.app.runtime_projection import ActiveRunConflictError, RuntimeProjectionService
from server.app.storage.backend import StorageBackend

if TYPE_CHECKING:
    from server.app.storage.artifact_store import ArtifactStore


@dataclass(frozen=True)
class SubmitTask:
    """Create a new durable task and its first execution attempt."""

    agent_name: str
    effective_scope: dict[str, str]
    content: str
    context_id: str | None = None
    idempotency_key: str | None = None
    message_id: str | None = None
    parent_message_id: str | None = None
    workspace_path: str | None = None
    session_config: SessionConfig = field(default_factory=SessionConfig)
    metadata: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None


@dataclass(frozen=True)
class ContinueTask:
    """Continue an interrupted durable task using a new execution attempt."""

    task_id: str
    agent_name: str
    effective_scope: dict[str, str]
    content: str
    idempotency_key: str | None = None
    message_id: str | None = None
    parent_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GetTask:
    """Look up one task within its trusted isolation boundary."""

    task_id: str
    agent_name: str
    effective_scope: dict[str, str]


@dataclass(frozen=True)
class ListTasks:
    """List tasks within one selected agent and exact effective scope."""

    agent_name: str
    effective_scope: dict[str, str]
    context_id: str | None = None
    statuses: set[TaskStatus] | None = None
    limit: int = 100
    cursor: str | None = None


@dataclass(frozen=True)
class CancelTask:
    """Cancel exactly one visible task."""

    task_id: str
    agent_name: str
    effective_scope: dict[str, str]


@dataclass(frozen=True)
class SubscribeTask:
    """Subscribe to durable events for one task."""

    task_id: str
    agent_name: str
    effective_scope: dict[str, str]
    after_sequence: int | None = None
    poll_interval: float = 0.1


@dataclass(frozen=True)
class TaskExecution:
    """Durable records required to execute one task attempt."""

    task: RuntimeTask
    session: Session
    run: SessionRun
    user_message: Message
    reused: bool = False


@dataclass(frozen=True)
class TaskInputArtifact:
    """Protocol-neutral, inert input attachment for one task execution."""

    id: str
    kind: str
    content: str
    content_encoding: str
    media_type: str | None
    filename: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TaskPage:
    """Cursor page of durable runtime tasks."""

    tasks: list[RuntimeTask]
    next_cursor: str | None


AbortExecution = Callable[[str, str], Awaitable[bool]]


class AgentTaskRuntime:
    """Own durable task identity, scope, attempts, and lifecycle transitions.

    This service deliberately does not invoke a model. Transport adapters and the
    native API execute the returned attempt through the configured agent runtime,
    while all lifecycle mutations remain centralized here.
    """

    def __init__(
        self,
        store: StorageBackend,
        *,
        default_workspace_path: str,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._store = store
        self._default_workspace_path = default_workspace_path
        self._artifact_store = artifact_store
        self.projection = RuntimeProjectionService(store)

    async def submit(self, command: SubmitTask) -> TaskExecution:
        """Create or idempotently recover a new task execution."""
        scope = dict(command.effective_scope)
        if command.idempotency_key:
            existing = await self._store.get_task_by_idempotency_key(
                command.agent_name,
                scope,
                command.idempotency_key,
            )
            if existing is not None:
                return await self._execution_for_existing(existing)

        context_id = command.context_id or str(uuid.uuid4())
        session = await self._store.get_session(context_id)
        if session is None:
            session = await self._store.create_session(
                session_id=context_id,
                thread_id=str(uuid.uuid4()),
                config=command.session_config,
                title=f"Agent context {context_id[:8]}",
                scopes=scope,
                agent_name=command.agent_name,
                metadata={"runtime_context_id": context_id},
                workspace_path=command.workspace_path or self._default_workspace_path,
            )
        else:
            self._assert_context_owner(session, command.agent_name, scope)

        active = await self._store.get_active_run(session.id)
        if active is not None:
            raise RuntimeTaskConflictError(
                f"Context '{context_id}' already has active run '{active.id}'"
            )

        task = await self._store.create_task(
            task_id=command.task_id or str(uuid.uuid4()),
            context_id=context_id,
            session_id=session.id,
            agent_name=command.agent_name,
            effective_scope=scope,
            idempotency_key=command.idempotency_key,
            metadata=dict(command.metadata),
        )
        return await self._begin_execution(
            task,
            session,
            content=command.content,
            message_id=command.message_id,
            parent_message_id=command.parent_message_id,
            idempotency_key=command.idempotency_key,
            metadata=command.metadata,
        )

    async def continue_task(self, command: ContinueTask) -> TaskExecution:
        """Create a new attempt under an input/auth-interrupted task."""
        task = await self._require_task(
            command.task_id,
            command.agent_name,
            command.effective_scope,
        )
        if command.idempotency_key:
            existing_run = await self._store.get_run_by_idempotency_key(
                task.session_id,
                command.idempotency_key,
            )
            if existing_run is not None:
                user_message = await self._message_for_run(task, existing_run)
                session = await self._require_context(task)
                return TaskExecution(task, session, existing_run, user_message, reused=True)
        if task.status not in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}:
            raise RuntimeTaskConflictError(
                f"Task '{task.id}' is not awaiting continuation",
                task_id=task.id,
            )
        session = await self._require_context(task)
        if await self._store.get_active_run(session.id) is not None:
            raise RuntimeTaskConflictError(
                f"Context '{session.id}' already has an active run",
                task_id=task.id,
            )
        return await self._begin_execution(
            task,
            session,
            content=command.content,
            message_id=command.message_id,
            parent_message_id=command.parent_message_id,
            idempotency_key=command.idempotency_key,
            metadata=command.metadata,
            parent_run_id=task.last_run_id,
        )

    async def get(self, query: GetTask) -> RuntimeTask | None:
        """Return a task only for its exact agent and effective scope."""
        return await self._store.get_task(
            query.task_id,
            dict(query.effective_scope),
            query.agent_name,
        )

    async def list(self, query: ListTasks) -> TaskPage:
        """List tasks without crossing agent or scope boundaries."""
        tasks, cursor = await self._store.list_tasks(
            query.agent_name,
            dict(query.effective_scope),
            context_id=query.context_id,
            statuses=query.statuses,
            limit=max(1, min(query.limit, 100)),
            cursor=query.cursor,
        )
        return TaskPage(tasks, cursor)

    async def cancel(
        self,
        command: CancelTask,
        *,
        abort_execution: AbortExecution | None = None,
    ) -> RuntimeTask:
        """Persist cancellation before requesting process-local interruption."""
        task = await self._require_task(
            command.task_id,
            command.agent_name,
            command.effective_scope,
        )
        if TaskStatus.is_terminal(task.status):
            raise RuntimeTaskNotCancelableError(task.id)
        canceled = await self._store.update_task(
            task.id,
            task.effective_scope,
            expected_statuses={task.status},
            status=TaskStatus.CANCELED,
            status_reason="Canceled by caller",
        )
        if canceled is None:
            winner = await self._require_task(
                command.task_id,
                command.agent_name,
                command.effective_scope,
            )
            if TaskStatus.is_terminal(winner.status):
                raise RuntimeTaskNotCancelableError(winner.id)
            raise RuntimeTaskConflictError(
                f"Task '{task.id}' changed while cancellation was requested",
                task_id=task.id,
            )

        run = await self._current_run(canceled)
        if run is not None and not RunStatus.is_terminal(run.status):
            if abort_execution is not None:
                await abort_execution(run.session_id, run.thread_id)
            await self.projection.transition_run(
                run,
                RunStatus.ABORTED,
                reason="Canceled by caller",
                error_code="CANCELED",
            )
        elif run is not None:
            # An input-required attempt is already terminal as an execution
            # attempt, but cancellation is still a new task-level transition.
            # Persist it so reconnecting subscribers observe the terminal
            # outcome instead of only noticing that polling stopped.
            await self.projection.append_event(
                run,
                "task.canceled",
                payload={"reason": "Canceled by caller"},
            )
        return canceled

    async def subscribe(self, query: SubscribeTask) -> AsyncIterator[SessionEvent]:
        """Yield ordered durable task events until the task becomes terminal."""
        task = await self._require_task(
            query.task_id,
            query.agent_name,
            query.effective_scope,
        )
        sequence = query.after_sequence
        while True:
            events = await self._store.list_events(
                task.session_id,
                after_sequence=sequence,
                limit=100,
                visibility="builder",
                task_id=task.id,
            )
            for event in events:
                sequence = event.sequence
                yield event
            current = await self._require_task(
                query.task_id,
                query.agent_name,
                query.effective_scope,
            )
            if TaskStatus.is_terminal(current.status) and not events:
                return
            await asyncio.sleep(max(0.01, query.poll_interval))

    async def transition(
        self,
        task: RuntimeTask,
        run: SessionRun,
        status: RunStatus,
        *,
        reason: str | None = None,
        error_code: str | None = None,
    ) -> tuple[RuntimeTask, SessionRun, SessionEvent]:
        """Apply one persisted run/task transition through the shared state machine."""
        updated_run, event = await self.projection.transition_run_with_event(
            run,
            status,
            reason=reason,
            error_code=error_code,
        )
        updated_task = await self._require_task(
            task.id,
            task.agent_name,
            task.effective_scope,
        )
        return updated_task, updated_run, event

    async def persist_assistant_message(
        self,
        execution: TaskExecution,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
        tool_calls: Sequence[ToolCall] | None = None,
        token_count: int | None = None,
        model_used: str | None = None,
        create_artifact: bool = True,
    ) -> Message:
        """Persist a task-correlated assistant output projection."""
        message = await self._store.create_message(
            message_id=message_id or str(uuid.uuid4()),
            session_id=execution.session.id,
            role="assistant",
            content=content,
            parent_id=execution.user_message.id,
            tool_calls=list(tool_calls) if tool_calls is not None else None,
            token_count=token_count,
            model_used=model_used,
            metadata={
                **(metadata or {}),
                "task_id": execution.task.id,
                "run_id": execution.run.id,
            },
        )
        await self.projection.refresh_message_count(execution.session.id)
        await self.projection.append_event(
            execution.run,
            "message.assistant.persisted",
            payload={"message_id": message.id, "content_length": len(content)},
        )
        if self._artifact_store is not None and create_artifact:
            from server.app.storage.config_models import ArtifactDefinition

            await self._artifact_store.upsert_artifact(
                ArtifactDefinition(
                    id=f"task-{execution.task.id}-response",
                    name="response",
                    artifact_type="artifact",
                    content=content,
                    content_type="text/plain",
                    run_id=execution.run.id,
                    visibility="run",
                    scope=execution.task.effective_scope,
                    source="api",
                )
            )
        return message

    async def persist_direct_message(
        self,
        execution: TaskExecution,
        *,
        content: str,
        media_type: str = "text/plain",
    ) -> Message:
        """Persist a message-only interaction while retaining idempotent recovery."""
        message = await self.persist_assistant_message(
            execution,
            content=content,
            metadata={"interaction_mode": "message", "media_type": media_type},
            create_artifact=False,
        )
        current = await self._require_task(
            execution.task.id,
            execution.task.agent_name,
            execution.task.effective_scope,
        )
        updated = await self._store.update_task(
            current.id,
            current.effective_scope,
            expected_statuses={current.status},
            metadata={
                **current.metadata,
                "interaction_mode": "message",
                "direct_message_id": message.id,
                "direct_message_media_type": media_type,
            },
        )
        if updated is None:
            raise RuntimeTaskConflictError(
                f"Task '{current.id}' changed while its response was persisted",
                task_id=current.id,
            )
        return message

    async def persist_artifact_output(
        self,
        execution: TaskExecution,
        *,
        artifact_id: str,
        name: str,
        kind: str,
        value: Any,
        media_type: str | None = None,
        filename: str | None = None,
        description: str | None = None,
        append: bool = False,
        last_chunk: bool = True,
    ) -> RuntimeTask:
        """Persist a JSON-safe, task-local artifact descriptor and durable event."""
        if kind not in {"text", "data", "raw", "url"}:
            raise ValueError(f"Unsupported artifact part kind: {kind}")
        encoded_value: Any = value
        if kind == "raw":
            raw = value if isinstance(value, bytes) else str(value).encode()
            encoded_value = base64.b64encode(raw).decode("ascii")
        part = {
            "kind": kind,
            "value": encoded_value,
            "media_type": media_type,
            "filename": filename,
        }
        current = await self._require_task(
            execution.task.id,
            execution.task.agent_name,
            execution.task.effective_scope,
        )
        metadata = dict(current.metadata)
        artifacts = [dict(item) for item in metadata.get("artifacts", [])]
        descriptor = next(
            (item for item in artifacts if item.get("artifact_id") == artifact_id),
            None,
        )
        if descriptor is None:
            descriptor = {
                "artifact_id": artifact_id,
                "name": name,
                "description": description,
                "parts": [],
                "last_chunk": False,
            }
            artifacts.append(descriptor)
        if append:
            descriptor["parts"] = [*descriptor.get("parts", []), part]
        else:
            descriptor["parts"] = [part]
        descriptor["name"] = name
        descriptor["description"] = description
        descriptor["last_chunk"] = last_chunk
        metadata["artifacts"] = artifacts
        updated = await self._store.update_task(
            current.id,
            current.effective_scope,
            expected_statuses={current.status},
            metadata=metadata,
        )
        if updated is None:
            raise RuntimeTaskConflictError(
                f"Task '{current.id}' changed while an artifact was persisted",
                task_id=current.id,
            )
        await self.projection.append_event(
            execution.run,
            "artifact.updated",
            payload={
                "artifact_id": artifact_id,
                "kind": kind,
                "append": append,
                "last_chunk": last_chunk,
            },
        )
        if self._artifact_store is not None:
            from server.app.storage.config_models import ArtifactDefinition

            content = (
                json.dumps(encoded_value, sort_keys=True) if kind == "data" else str(encoded_value)
            )
            await self._artifact_store.upsert_artifact(
                ArtifactDefinition(
                    id=artifact_id,
                    name=name,
                    artifact_type="artifact",
                    content=content,
                    content_type=media_type or _default_media_type(kind),
                    run_id=execution.run.id,
                    visibility="run",
                    scope=execution.task.effective_scope,
                    source="api",
                )
            )
        return updated

    async def persist_input_artifacts(
        self,
        execution: TaskExecution,
        artifacts: Sequence[TaskInputArtifact],
    ) -> RuntimeTask:
        """Persist inert, task-linked inputs under the execution's exact scope."""
        if not artifacts:
            return execution.task

        current = await self._require_task(
            execution.task.id,
            execution.task.agent_name,
            execution.task.effective_scope,
        )
        new_descriptors = [
            {
                "artifact_id": artifact.id,
                "kind": artifact.kind,
                "content_encoding": artifact.content_encoding,
                "filename": artifact.filename,
                "media_type": artifact.media_type,
                "metadata": artifact.metadata,
            }
            for artifact in artifacts
        ]
        descriptors = [dict(item) for item in current.metadata.get("input_artifacts", [])]
        new_ids = {item["artifact_id"] for item in new_descriptors}
        descriptors = [item for item in descriptors if item.get("artifact_id") not in new_ids]
        descriptors.extend(new_descriptors)
        updated = await self._store.update_task(
            current.id,
            current.effective_scope,
            expected_statuses={current.status},
            metadata={**current.metadata, "input_artifacts": descriptors},
        )
        if updated is None:
            raise RuntimeTaskConflictError(
                f"Task '{current.id}' changed while input artifacts were persisted",
                task_id=current.id,
            )

        if self._artifact_store is not None:
            from server.app.storage.config_models import ArtifactDefinition

            for artifact in artifacts:
                default_type = (
                    "application/octet-stream" if artifact.kind == "raw" else "text/uri-list"
                )
                await self._artifact_store.upsert_artifact(
                    ArtifactDefinition(
                        id=artifact.id,
                        name=artifact.id,
                        artifact_type="artifact",
                        content=artifact.content,
                        content_type=artifact.media_type or default_type,
                        run_id=execution.run.id,
                        visibility="run",
                        scope=execution.task.effective_scope,
                        source="api",
                    )
                )
        await self.projection.append_event(
            execution.run,
            "artifact.input.persisted",
            payload={"count": len(artifacts)},
            visibility="internal",
        )
        return updated

    async def _begin_execution(
        self,
        task: RuntimeTask,
        session: Session,
        *,
        content: str,
        message_id: str | None,
        parent_message_id: str | None,
        idempotency_key: str | None,
        metadata: dict[str, Any],
        parent_run_id: str | None = None,
    ) -> TaskExecution:
        try:
            run = await self.projection.begin_run(
                session=session,
                effective_scope=task.effective_scope,
                idempotency_key=idempotency_key,
                metadata={**metadata, "task_id": task.id},
                task_id=task.id,
                parent_run_id=parent_run_id,
            )
        except ActiveRunConflictError as exc:
            await self._store.update_task(
                task.id,
                task.effective_scope,
                expected_statuses={task.status},
                status=TaskStatus.REJECTED,
                status_reason=str(exc),
            )
            raise RuntimeTaskConflictError(str(exc), task_id=task.id) from exc
        user_message = await self._store.create_message(
            message_id=message_id or str(uuid.uuid4()),
            session_id=session.id,
            role="user",
            content=content,
            parent_id=parent_message_id,
            metadata={
                **metadata,
                "task_id": task.id,
                "run_id": run.id,
            },
        )
        await self.projection.append_event(
            run,
            "message.user.accepted",
            payload={"message_id": user_message.id, "content_length": len(content)},
        )
        await self.projection.refresh_message_count(session.id)
        current = await self._require_task(task.id, task.agent_name, task.effective_scope)
        return TaskExecution(current, session, run, user_message)

    async def _execution_for_existing(self, task: RuntimeTask) -> TaskExecution:
        run = await self._current_run(task)
        if run is None:
            raise RuntimeTaskConflictError(
                f"Idempotent task '{task.id}' has no execution attempt",
                task_id=task.id,
            )
        session = await self._require_context(task)
        user_message = await self._message_for_run(task, run)
        return TaskExecution(task, session, run, user_message, reused=True)

    async def _message_for_run(self, task: RuntimeTask, run: SessionRun) -> Message:
        messages = await self._store.list_messages_for_session(task.session_id)
        for message in messages:
            metadata = message.metadata or {}
            if message.role == "user" and metadata.get("run_id") == run.id:
                return message
        raise RuntimeTaskConflictError(
            f"Task '{task.id}' execution '{run.id}' has no input message",
            task_id=task.id,
        )

    async def _current_run(self, task: RuntimeTask) -> SessionRun | None:
        run_id = task.current_run_id or task.last_run_id
        return await self._store.get_run(run_id) if run_id is not None else None

    async def _require_context(self, task: RuntimeTask) -> Session:
        session = await self._store.get_session(task.session_id)
        if session is None:
            raise RuntimeTaskConflictError(
                f"Task '{task.id}' context is unavailable",
                task_id=task.id,
            )
        self._assert_context_owner(session, task.agent_name, task.effective_scope)
        return session

    async def _require_task(
        self,
        task_id: str,
        agent_name: str,
        effective_scope: dict[str, str],
    ) -> RuntimeTask:
        task = await self._store.get_task(task_id, dict(effective_scope), agent_name)
        if task is None:
            raise RuntimeTaskNotFoundError(task_id)
        return task

    @staticmethod
    def _assert_context_owner(
        session: Session,
        agent_name: str,
        effective_scope: dict[str, str],
    ) -> None:
        if session.agent_name != agent_name or (session.scopes or {}) != effective_scope:
            # Deliberately conceal cross-agent and cross-scope ownership.
            raise RuntimeTaskNotFoundError(session.id)


def _default_media_type(kind: str) -> str:
    if kind == "data":
        return "application/json"
    if kind == "raw":
        return "application/octet-stream"
    return "text/plain"
