"""A2A executor translating requests onto Cognition's neutral task runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from a2a.helpers.proto_helpers import (
    new_data_artifact_update_event,
    new_raw_artifact_update_event,
    new_text_artifact_update_event,
    new_text_message,
    new_text_status_update_event,
    new_url_artifact_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError

from server.app.agent.runtime import (
    ArtifactEvent,
    DirectMessageEvent,
    DoneEvent,
    ErrorEvent,
    InterruptEvent,
    RejectedEvent,
    RunStateEvent,
    TokenEvent,
)
from server.app.agent.task_runtime import (
    AgentTaskRuntime,
    CancelTask,
    ContinueTask,
    GetTask,
    SubmitTask,
    TaskExecution,
)
from server.app.exceptions import RuntimeTaskConflictError, RuntimeTaskNotFoundError
from server.app.models import RunStatus, TaskStatus
from server.app.protocols.a2a.inbound import InvalidA2APartError, normalize_a2a_parts
from server.app.protocols.a2a.task_store import (
    CognitionTaskStore,
    effective_scope_from_context,
)

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager

logger = structlog.get_logger(__name__)

_ARTIFACT_NAME = "response"
_TASK_STATUS_TO_A2A: dict[TaskStatus, int] = {
    TaskStatus.SUBMITTED: TaskState.TASK_STATE_SUBMITTED,
    TaskStatus.WORKING: TaskState.TASK_STATE_WORKING,
    TaskStatus.INPUT_REQUIRED: TaskState.TASK_STATE_INPUT_REQUIRED,
    TaskStatus.AUTH_REQUIRED: TaskState.TASK_STATE_AUTH_REQUIRED,
    TaskStatus.COMPLETED: TaskState.TASK_STATE_COMPLETED,
    TaskStatus.FAILED: TaskState.TASK_STATE_FAILED,
    TaskStatus.CANCELED: TaskState.TASK_STATE_CANCELED,
    TaskStatus.REJECTED: TaskState.TASK_STATE_REJECTED,
}


class CognitionA2AExecutor(AgentExecutor):
    """Run one A2A request through the shared durable Cognition lifecycle."""

    def __init__(
        self,
        runtime: AgentTaskRuntime,
        task_store: CognitionTaskStore,
        session_agent_manager: SessionAgentManager,
        *,
        agent_name: str,
        message_id_idempotency: bool = True,
        max_raw_part_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._runtime = runtime
        self._task_store = task_store
        self._agent_manager = session_agent_manager
        self._agent_name = agent_name
        self._message_id_idempotency = message_id_idempotency
        self._max_raw_part_bytes = max_raw_part_bytes

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create/continue a task, stream execution, and persist before emitting."""
        if context.call_context is None or not context.task_id or not context.context_id:
            raise InvalidParamsError(message="Task and context identifiers are required")
        scope = effective_scope_from_context(context.call_context)
        message_id = context.message.message_id if context.message else None
        try:
            normalized = normalize_a2a_parts(
                context.message.parts if context.message else (),
                task_id=context.task_id,
                message_id=message_id,
                max_raw_part_bytes=self._max_raw_part_bytes,
            )
        except InvalidA2APartError as exc:
            raise InvalidParamsError(message=str(exc)) from exc
        user_text = normalized.content or "(empty message)"
        persisted_message_id = message_id if self._message_id_idempotency else None
        idempotency_key = message_id or context.task_id if self._message_id_idempotency else None
        execution: TaskExecution | None = None

        try:
            existing = await self._runtime.get(GetTask(context.task_id, self._agent_name, scope))
            if existing is None:
                execution = await self._runtime.submit(
                    SubmitTask(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        agent_name=self._agent_name,
                        effective_scope=scope,
                        content=user_text,
                        message_id=persisted_message_id or None,
                        idempotency_key=idempotency_key,
                        metadata={
                            "source": "a2a-jsonrpc",
                            "a2a_message_id": message_id,
                        },
                    )
                )
            else:
                if existing.context_id != context.context_id:
                    raise RuntimeTaskNotFoundError(context.task_id)
                execution = await self._runtime.continue_task(
                    ContinueTask(
                        task_id=existing.id,
                        agent_name=self._agent_name,
                        effective_scope=scope,
                        content=user_text,
                        message_id=persisted_message_id or None,
                        idempotency_key=idempotency_key,
                        metadata={
                            "source": "a2a-jsonrpc",
                            "a2a_message_id": message_id,
                        },
                    )
                )

            if execution.reused:
                if execution.task.metadata.get("interaction_mode") == "message":
                    message = await self._task_store.project_message(execution.task)
                    if message is not None:
                        await event_queue.enqueue_event(message)
                        return
                await event_queue.enqueue_event(await self._task_store.project(execution.task))
                return

            await self._runtime.persist_input_artifacts(execution, normalized.artifacts)

            service = self._agent_manager.get_service(execution.session.id)
            if service is None:
                service = self._agent_manager.register_session(
                    execution.session.id,
                    execution.session.workspace_path,
                )

            accumulated_text: list[str] = []
            task_announced = False
            has_artifact = False
            system_prompt = execution.session.config.system_prompt
            async for event in service.stream_response(
                session_id=execution.session.id,
                thread_id=execution.session.thread_id,
                project_path=execution.session.workspace_path,
                content=user_text,
                system_prompt=system_prompt,
                manager=self._agent_manager,
                scope=scope,
                run_id=None,
            ):
                current = await self._runtime.get(
                    GetTask(execution.task.id, self._agent_name, scope)
                )
                if current is None:
                    raise RuntimeTaskNotFoundError(execution.task.id)
                if current.status == TaskStatus.CANCELED:
                    await self._agent_manager.abort_session(
                        execution.session.id,
                        execution.session.thread_id,
                    )
                    await event_queue.enqueue_event(
                        _status_event(execution, TaskState.TASK_STATE_CANCELED, "Canceled")
                    )
                    return

                if isinstance(event, DirectMessageEvent):
                    persisted_message = await self._runtime.persist_direct_message(
                        execution,
                        content=event.content,
                        media_type=event.media_type,
                    )
                    await self._runtime.transition(
                        execution.task,
                        execution.run,
                        RunStatus.DONE,
                    )
                    projected = new_text_message(
                        event.content,
                        media_type=event.media_type,
                        context_id=execution.task.context_id,
                        role=Role.ROLE_AGENT,
                    )
                    projected.message_id = persisted_message.id
                    await event_queue.enqueue_event(projected)
                    return

                if not task_announced:
                    await event_queue.enqueue_event(await self._task_store.project(current))
                    task_announced = True

                if isinstance(event, ArtifactEvent):
                    await self._runtime.persist_artifact_output(
                        execution,
                        artifact_id=event.artifact_id,
                        name=event.name,
                        kind=event.kind,
                        value=event.value,
                        media_type=event.media_type,
                        filename=event.filename,
                        description=event.description,
                        append=event.append,
                        last_chunk=event.last_chunk,
                    )
                    await event_queue.enqueue_event(_artifact_event(execution, event))
                    has_artifact = True
                    continue

                if isinstance(event, TokenEvent):
                    accumulated_text.append(event.content)
                    await self._runtime.projection.append_event(
                        execution.run,
                        "message.assistant.delta",
                        payload={"length": len(event.content)},
                        visibility="internal",
                    )
                    continue

                if isinstance(event, InterruptEvent) or (
                    isinstance(event, RunStateEvent) and event.to_status == "waiting_for_approval"
                ):
                    _task, _run, _event = await self._runtime.transition(
                        execution.task,
                        execution.run,
                        RunStatus.INTERRUPTED,
                        reason="Human input required",
                    )
                    await event_queue.enqueue_event(
                        _status_event(
                            execution,
                            TaskState.TASK_STATE_INPUT_REQUIRED,
                            "Human input required",
                        )
                    )
                    return

                if isinstance(event, ErrorEvent):
                    if event.code == "ABORTED":
                        status = RunStatus.ABORTED
                        state = TaskState.TASK_STATE_CANCELED
                    else:
                        status = RunStatus.FAILED
                        state = TaskState.TASK_STATE_FAILED
                    await self._runtime.transition(
                        execution.task,
                        execution.run,
                        status,
                        reason=event.message,
                        error_code=event.code,
                    )
                    await event_queue.enqueue_event(
                        _status_event(execution, state, event.message or status.value)
                    )
                    return

                if isinstance(event, RejectedEvent):
                    await self._runtime.transition(
                        execution.task,
                        execution.run,
                        RunStatus.REJECTED,
                        reason=event.reason,
                        error_code="REJECTED",
                    )
                    await event_queue.enqueue_event(
                        _status_event(
                            execution,
                            TaskState.TASK_STATE_REJECTED,
                            event.reason,
                        )
                    )
                    return

                if isinstance(event, DoneEvent) or (
                    isinstance(event, RunStateEvent) and event.to_status == "done"
                ):
                    await self._complete(
                        execution,
                        event_queue,
                        accumulated_text,
                        has_artifact=has_artifact,
                    )
                    return

            if not task_announced:
                await event_queue.enqueue_event(await self._task_store.project(execution.task))
            await self._complete(
                execution,
                event_queue,
                accumulated_text,
                has_artifact=has_artifact,
            )
        except RuntimeTaskNotFoundError as exc:
            raise TaskNotFoundError from exc
        except RuntimeTaskConflictError as exc:
            raise InvalidParamsError(message=str(exc)) from exc
        except Exception:
            logger.exception(
                "A2A task execution failed",
                task_id=context.task_id,
                agent_name=self._agent_name,
            )
            if execution is not None:
                await self._runtime.transition(
                    execution.task,
                    execution.run,
                    RunStatus.FAILED,
                    reason="Agent execution failed",
                    error_code="AGENT_ERROR",
                )
                await event_queue.enqueue_event(
                    _status_event(
                        execution,
                        TaskState.TASK_STATE_FAILED,
                        "Agent execution failed",
                    )
                )
                return
            raise

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel exactly the task identified by the A2A request context."""
        if context.call_context is None or not context.task_id:
            raise TaskNotFoundError
        scope = effective_scope_from_context(context.call_context)
        try:
            task = await self._runtime.cancel(
                CancelTask(context.task_id, self._agent_name, scope),
                abort_execution=self._agent_manager.abort_session,
            )
        except RuntimeTaskNotFoundError as exc:
            raise TaskNotFoundError from exc
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_CANCELED,
                text="Canceled",
            )
        )

    async def _complete(
        self,
        execution: TaskExecution,
        event_queue: EventQueue,
        accumulated_text: list[str],
        *,
        has_artifact: bool,
    ) -> None:
        text = "".join(accumulated_text) or "(no response)"
        await self._runtime.persist_assistant_message(
            execution,
            content=text,
            create_artifact=not has_artifact,
        )
        task, _run, _event = await self._runtime.transition(
            execution.task,
            execution.run,
            RunStatus.DONE,
        )
        if task.status == TaskStatus.CANCELED:
            await event_queue.enqueue_event(
                _status_event(execution, TaskState.TASK_STATE_CANCELED, "Canceled")
            )
            return
        if not has_artifact:
            await event_queue.enqueue_event(
                new_text_artifact_update_event(
                    task_id=execution.task.id,
                    context_id=execution.task.context_id,
                    name=_ARTIFACT_NAME,
                    text=text,
                    artifact_id=f"task-{execution.task.id}-response",
                    last_chunk=True,
                )
            )
        await event_queue.enqueue_event(
            _status_event(execution, TaskState.TASK_STATE_COMPLETED, "Done")
        )


def _status_event(
    execution: TaskExecution,
    state: int,
    text: str,
) -> TaskStatusUpdateEvent:
    return new_text_status_update_event(
        task_id=execution.task.id,
        context_id=execution.task.context_id,
        state=state,  # type: ignore[arg-type]
        text=text,
    )


def _artifact_event(
    execution: TaskExecution,
    event: ArtifactEvent,
) -> TaskArtifactUpdateEvent:
    if event.kind == "data":
        return new_data_artifact_update_event(
            task_id=execution.task.id,
            context_id=execution.task.context_id,
            name=event.name,
            data=event.value,
            media_type=event.media_type,
            append=event.append,
            last_chunk=event.last_chunk,
            artifact_id=event.artifact_id,
        )
    if event.kind == "raw":
        raw = event.value if isinstance(event.value, bytes) else str(event.value).encode()
        return new_raw_artifact_update_event(
            task_id=execution.task.id,
            context_id=execution.task.context_id,
            name=event.name,
            raw=raw,
            media_type=event.media_type,
            filename=event.filename,
            append=event.append,
            last_chunk=event.last_chunk,
            artifact_id=event.artifact_id,
        )
    if event.kind == "url":
        return new_url_artifact_update_event(
            task_id=execution.task.id,
            context_id=execution.task.context_id,
            name=event.name,
            url=str(event.value),
            media_type=event.media_type,
            filename=event.filename,
            append=event.append,
            last_chunk=event.last_chunk,
            artifact_id=event.artifact_id,
        )
    return new_text_artifact_update_event(
        task_id=execution.task.id,
        context_id=execution.task.context_id,
        name=event.name,
        text=str(event.value),
        append=event.append,
        last_chunk=event.last_chunk,
        artifact_id=event.artifact_id,
    )
