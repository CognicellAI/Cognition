"""A2A executor translating requests onto Cognition's neutral task runtime."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

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
from a2a.utils.errors import (
    ContentTypeNotSupportedError,
    InvalidParamsError,
    TaskNotFoundError,
)
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.agent.definition import A2AConfig
from server.app.agent.runtime import (
    ArtifactEvent,
    DirectMessageEvent,
    DoneEvent,
    ErrorEvent,
    InterruptEvent,
    RejectedEvent,
    RunStateEvent,
    TokenEvent,
    UsageEvent,
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
from server.app.observability import (
    A2A_STREAM_CHUNK_BYTES,
    A2A_STREAM_FLUSH_DURATION,
    A2UI_BATCH_MESSAGES,
    RUNTIME_ACTIVE_TASKS,
    RUNTIME_TASK_DURATION,
    RUNTIME_TASK_TRANSITIONS_TOTAL,
    RUNTIME_TIME_TO_FIRST_OUTPUT,
    agent_run_span,
    current_trace_context,
)
from server.app.protocols.a2a.a2ui import (
    A2UI_EXTENSION_URI,
    A2UI_MEDIA_TYPE,
    A2UIValidationError,
    build_unknown_agent_function_responses,
    has_agent_function_calls,
    negotiate_a2ui,
)
from server.app.protocols.a2a.inbound import (
    InvalidA2APartError,
    UnsupportedA2AMediaTypeError,
    normalize_a2a_parts,
    validate_a2a_part_media_types,
)
from server.app.protocols.a2a.task_store import (
    CognitionTaskStore,
    effective_scope_from_context,
)

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager

logger = structlog.get_logger(__name__)

_ARTIFACT_NAME = "response"
_FLUSH_TICK = object()
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
        a2a_config: A2AConfig | None = None,
        supported_input_modes: tuple[str, ...] = ("text/plain", "application/json"),
        message_id_idempotency: bool = True,
        max_raw_part_bytes: int = 10 * 1024 * 1024,
        max_parts: int = 64,
        max_message_bytes: int = 16 * 1024 * 1024,
        max_text_part_bytes: int = 2 * 1024 * 1024,
        max_data_part_bytes: int = 2 * 1024 * 1024,
        max_output_artifacts: int = 100,
        max_output_bytes: int = 16 * 1024 * 1024,
        stream_chunk_bytes: int = 4096,
        stream_flush_interval_seconds: float = 0.25,
    ) -> None:
        self._runtime = runtime
        self._task_store = task_store
        self._agent_manager = session_agent_manager
        self._agent_name = agent_name
        self._a2a_config = a2a_config or A2AConfig()
        self._supported_input_modes = supported_input_modes
        self._message_id_idempotency = message_id_idempotency
        self._max_raw_part_bytes = max_raw_part_bytes
        self._max_parts = max_parts
        self._max_message_bytes = max_message_bytes
        self._max_text_part_bytes = max_text_part_bytes
        self._max_data_part_bytes = max_data_part_bytes
        self._max_output_artifacts = max_output_artifacts
        self._max_output_bytes = max_output_bytes
        self._stream_chunk_bytes = stream_chunk_bytes
        self._stream_flush_interval = stream_flush_interval_seconds

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Create/continue a task, stream execution, and persist before emitting."""
        if context.call_context is None or not context.task_id or not context.context_id:
            raise InvalidParamsError(message="Task and context identifiers are required")
        scope = effective_scope_from_context(context.call_context)
        message_id = context.message.message_id if context.message else None
        message_parts = tuple(context.message.parts if context.message else ())
        message_metadata = (
            MessageToDict(context.message.metadata)
            if context.message and context.message.HasField("metadata")
            else {}
        )
        a2ui_context = None
        try:
            validate_a2a_part_media_types(
                message_parts,
                self._supported_input_modes,
            )
            a2ui_context = negotiate_a2ui(
                config=self._a2a_config,
                requested_extensions=tuple(
                    context.call_context.state.get("a2a_requested_extensions", ())
                ),
                message_metadata=message_metadata,
                message_parts=message_parts,
                compatibility_alias_used=bool(
                    context.call_context.state.get("a2a_extension_alias_used")
                ),
            )
            normalized = normalize_a2a_parts(
                message_parts,
                task_id=context.task_id,
                message_id=message_id,
                max_raw_part_bytes=self._max_raw_part_bytes,
                max_parts=self._max_parts,
                max_message_bytes=self._max_message_bytes,
                max_text_part_bytes=self._max_text_part_bytes,
                max_data_part_bytes=self._max_data_part_bytes,
                message_metadata=message_metadata,
                message_extensions=(tuple(context.message.extensions) if context.message else ()),
                reference_task_ids=(
                    tuple(context.message.reference_task_ids) if context.message else ()
                ),
            )
        except UnsupportedA2AMediaTypeError as exc:
            raise ContentTypeNotSupportedError(message=str(exc)) from exc
        except InvalidA2APartError as exc:
            from server.app.observability import A2A_LIMIT_REJECTIONS_TOTAL

            A2A_LIMIT_REJECTIONS_TOTAL.labels(direction="input", limit="message").inc()
            raise InvalidParamsError(message=str(exc)) from exc
        except A2UIValidationError as exc:
            raise InvalidParamsError(message=str(exc)) from exc
        user_text = normalized.content or "(empty message)"
        persisted_message_id = message_id if self._message_id_idempotency else None
        idempotency_key = message_id or context.task_id if self._message_id_idempotency else None
        execution: TaskExecution | None = None
        active_metric = False
        run_span_context: Any | None = None
        run_span: Any | None = None
        execution_started = time.monotonic()

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
                            "a2a_request_fingerprint": context.call_context.state.get(
                                "a2a_request_fingerprint"
                            ),
                            **(
                                {"a2ui": a2ui_context.to_metadata()}
                                if a2ui_context is not None
                                else {}
                            ),
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
                            "a2a_request_fingerprint": context.call_context.state.get(
                                "a2a_request_fingerprint"
                            ),
                            **(
                                {"a2ui": a2ui_context.to_metadata()}
                                if a2ui_context is not None
                                else {}
                            ),
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

            RUNTIME_ACTIVE_TASKS.labels(transport="a2a").inc()
            active_metric = True

            await self._runtime.persist_input_parts(
                execution,
                normalized.parts,
                message_id=normalized.message_id,
                message_metadata=normalized.metadata,
                message_extensions=normalized.extensions,
                reference_task_ids=normalized.reference_task_ids,
            )
            if a2ui_context is not None and has_agent_function_calls(a2ui_context):
                messages = build_unknown_agent_function_responses(a2ui_context)
                A2UI_BATCH_MESSAGES.observe(len(messages))
                artifact = ArtifactEvent(
                    artifact_id=f"a2ui-{execution.run.id}",
                    name="a2ui",
                    kind="data",
                    value=messages,
                    media_type=A2UI_MEDIA_TYPE,
                    description="A2UI v1.0 message batch",
                    extensions=(A2UI_EXTENSION_URI,),
                )
                await self._runtime.persist_artifact_output(
                    execution,
                    artifact_id=artifact.artifact_id,
                    name=artifact.name,
                    kind=artifact.kind,
                    value=artifact.value,
                    media_type=artifact.media_type,
                    filename=artifact.filename,
                    description=artifact.description,
                    extensions=artifact.extensions,
                    append=artifact.append,
                    last_chunk=artifact.last_chunk,
                )
                await event_queue.enqueue_event(_artifact_event(execution, artifact))
                await self._complete(
                    execution,
                    event_queue,
                    [],
                    has_artifact=True,
                )
                return

            service = self._agent_manager.get_service(execution.session.id)
            if service is None:
                service = self._agent_manager.register_session(
                    execution.session.id,
                    execution.session.workspace_path,
                )

            accumulated_text: list[str] = []
            chunk_buffer: list[str] = []
            task_announced = False
            has_artifact = False
            streamed_chunk = False
            output_bytes = 0
            output_artifact_ids: set[str] = set()
            started_at = time.monotonic()
            last_flush_at = started_at
            first_output_recorded = False

            async def flush_text_chunk(*, last_chunk: bool) -> None:
                nonlocal streamed_chunk, last_flush_at, first_output_recorded
                text = "".join(chunk_buffer)
                if not text and not (last_chunk and streamed_chunk):
                    return
                chunk_buffer.clear()
                flush_started = time.monotonic()
                artifact_id = f"task-{execution.task.id}-response"
                await self._runtime.persist_artifact_output(
                    execution,
                    artifact_id=artifact_id,
                    name=_ARTIFACT_NAME,
                    kind="text",
                    value=text,
                    media_type="text/plain",
                    append=streamed_chunk,
                    last_chunk=last_chunk,
                )
                await event_queue.enqueue_event(
                    new_text_artifact_update_event(
                        task_id=execution.task.id,
                        context_id=execution.task.context_id,
                        name=_ARTIFACT_NAME,
                        text=text,
                        artifact_id=artifact_id,
                        append=streamed_chunk,
                        last_chunk=last_chunk,
                    )
                )
                A2A_STREAM_CHUNK_BYTES.observe(len(text.encode("utf-8")))
                A2A_STREAM_FLUSH_DURATION.observe(time.monotonic() - flush_started)
                if not first_output_recorded:
                    RUNTIME_TIME_TO_FIRST_OUTPUT.labels(transport="a2a").observe(
                        time.monotonic() - started_at
                    )
                    first_output_recorded = True
                streamed_chunk = True
                last_flush_at = time.monotonic()

            system_prompt = execution.session.config.system_prompt
            span_context = agent_run_span(
                session_id=execution.session.id,
                run_id=execution.run.id,
                thread_id=execution.session.thread_id,
                scope_keys=sorted(scope),
                agent_name=execution.session.agent_name,
                agent_revision=execution.run.agent_revision,
                manifest_digest=execution.run.manifest_digest,
                parent_run_id=execution.run.parent_run_id,
                effective_scope=scope,
                transport="a2a",
            )
            run_span = span_context.__enter__()
            run_span_context = span_context
            if run_span is not None:
                run_span.set_attribute(
                    "gen_ai.input.messages",
                    json.dumps(
                        [{"role": "user", "content": user_text}],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                run_span.add_event(
                    "cognition.message.user.received",
                    {
                        "cognition.message.input_bytes": len(
                            user_text.encode("utf-8")
                        )
                    },
                )
            trace_id, _span_id = current_trace_context(run_span)
            if trace_id and execution.run.trace_id != trace_id:
                updated_run = await self._runtime.projection.store.update_run(
                    execution.run.id,
                    trace_id=trace_id,
                    effective_scope=execution.run.effective_scope,
                )
                if updated_run is not None:
                    execution.run.trace_id = updated_run.trace_id

            source = service.stream_response(
                session_id=execution.session.id,
                thread_id=execution.session.thread_id,
                project_path=execution.session.workspace_path,
                content=user_text,
                system_prompt=system_prompt,
                manager=self._agent_manager,
                scope=scope,
                run_id=execution.run.id,
                trace_parent_span=run_span,
            )
            async for event in _with_flush_ticks(source, self._stream_flush_interval):
                if event is _FLUSH_TICK:
                    await flush_text_chunk(last_chunk=False)
                    has_artifact = has_artifact or streamed_chunk
                    continue
                current = await self._runtime.get(
                    GetTask(execution.task.id, self._agent_name, scope)
                )
                if current is None:
                    raise RuntimeTaskNotFoundError(execution.task.id)
                if current.status == TaskStatus.CANCELED:
                    if run_span is not None:
                        run_span.add_event("cognition.run.canceled")
                    await self._agent_manager.abort_session(
                        execution.session.id,
                        execution.session.thread_id,
                    )
                    await event_queue.enqueue_event(
                        _status_event(execution, TaskState.TASK_STATE_CANCELED, "Canceled")
                    )
                    return

                if isinstance(event, DirectMessageEvent):
                    if run_span is not None:
                        run_span.set_attribute(
                            "gen_ai.output.messages",
                            json.dumps(
                                [{"role": "assistant", "content": event.content}],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                        run_span.add_event(
                            "cognition.run.completed",
                            {
                                "cognition.run.terminal_status": "done",
                                "cognition.message.output_bytes": len(
                                    event.content.encode("utf-8")
                                ),
                            },
                        )
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
                    artifact_bytes = _artifact_size(event.kind, event.value)
                    output_bytes += artifact_bytes
                    output_artifact_ids.add(event.artifact_id)
                    if output_bytes > self._max_output_bytes:
                        await self._fail_output_limit(
                            execution, event_queue, "OUTPUT_BYTES_EXCEEDED"
                        )
                        return
                    if len(output_artifact_ids) > self._max_output_artifacts:
                        await self._fail_output_limit(
                            execution, event_queue, "OUTPUT_ARTIFACTS_EXCEEDED"
                        )
                        return
                    await self._runtime.persist_artifact_output(
                        execution,
                        artifact_id=event.artifact_id,
                        name=event.name,
                        kind=event.kind,
                        value=event.value,
                        media_type=event.media_type,
                        filename=event.filename,
                        description=event.description,
                        extensions=event.extensions,
                        append=event.append,
                        last_chunk=event.last_chunk,
                    )
                    await event_queue.enqueue_event(_artifact_event(execution, event))
                    has_artifact = True
                    continue

                if isinstance(event, TokenEvent):
                    accumulated_text.append(event.content)
                    chunk_buffer.append(event.content)
                    output_bytes += len(event.content.encode("utf-8"))
                    if output_bytes > self._max_output_bytes:
                        await self._fail_output_limit(
                            execution, event_queue, "OUTPUT_BYTES_EXCEEDED"
                        )
                        return
                    buffered_bytes = len("".join(chunk_buffer).encode("utf-8"))
                    elapsed = time.monotonic() - last_flush_at
                    if (
                        buffered_bytes >= self._stream_chunk_bytes
                        or elapsed >= self._stream_flush_interval
                    ):
                        await flush_text_chunk(last_chunk=False)
                        has_artifact = True
                    continue

                if isinstance(event, InterruptEvent) or (
                    isinstance(event, RunStateEvent) and event.to_status == "waiting_for_approval"
                ):
                    if run_span is not None:
                        run_span.add_event("cognition.hitl.interrupt")
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
                    if run_span is not None:
                        run_span.add_event(
                            "cognition.run.failed",
                            {
                                "cognition.error.code": event.code,
                                "cognition.error.message_bytes": len(
                                    event.message.encode("utf-8")
                                ),
                            },
                        )
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
                    if run_span is not None:
                        run_span.add_event(
                            "cognition.run.rejected",
                            {
                                "cognition.run.reason_bytes": len(
                                    event.reason.encode("utf-8")
                                )
                            },
                        )
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

                if isinstance(event, UsageEvent):
                    if run_span is not None:
                        usage_attributes: dict[str, str | int] = {
                            "cognition.usage.source": event.source,
                            "cognition.usage.status": event.status,
                            "cognition.usage.model_calls": event.model_calls,
                            "cognition.usage.reported_model_calls": (
                                event.reported_model_calls
                            ),
                            "cognition.usage.unreported_model_calls": (
                                event.unreported_model_calls
                            ),
                        }
                        for key, value in (
                            ("cognition.usage.input_tokens", event.input_tokens),
                            ("cognition.usage.output_tokens", event.output_tokens),
                            ("cognition.usage.total_tokens", event.total_tokens),
                        ):
                            if value is not None:
                                usage_attributes[key] = value
                        for key, attribute_value in usage_attributes.items():
                            run_span.set_attribute(key, attribute_value)
                        run_span.add_event(
                            "cognition.usage.recorded",
                            usage_attributes,
                        )
                    continue

                if isinstance(event, DoneEvent) or (
                    isinstance(event, RunStateEvent) and event.to_status == "done"
                ):
                    await flush_text_chunk(last_chunk=True)
                    if run_span is not None:
                        output = "".join(accumulated_text)
                        run_span.set_attribute(
                            "gen_ai.output.messages",
                            json.dumps(
                                [{"role": "assistant", "content": output}],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                        run_span.add_event(
                            "cognition.run.completed",
                            {
                                "cognition.run.terminal_status": "done",
                                "cognition.message.output_bytes": len(
                                    output.encode("utf-8")
                                ),
                            },
                        )
                    await self._complete(
                        execution,
                        event_queue,
                        accumulated_text,
                        has_artifact=has_artifact or streamed_chunk,
                    )
                    return

            if not task_announced:
                await event_queue.enqueue_event(await self._task_store.project(execution.task))
            await flush_text_chunk(last_chunk=True)
            await self._complete(
                execution,
                event_queue,
                accumulated_text,
                has_artifact=has_artifact or streamed_chunk,
            )
        except RuntimeTaskNotFoundError as exc:
            raise TaskNotFoundError from exc
        except RuntimeTaskConflictError as exc:
            raise InvalidParamsError(message=str(exc)) from exc
        except Exception:
            if run_span is not None:
                run_span.add_event("cognition.run.exception")
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
        finally:
            if run_span_context is not None:
                run_span_context.__exit__(None, None, None)
            if active_metric:
                RUNTIME_ACTIVE_TASKS.labels(transport="a2a").dec()
                final_task = await self._runtime.get(
                    GetTask(context.task_id, self._agent_name, scope)
                )
                outcome = final_task.status.value if final_task is not None else "unknown"
                RUNTIME_TASK_DURATION.labels(transport="a2a", outcome=outcome).observe(
                    time.monotonic() - execution_started
                )
                RUNTIME_TASK_TRANSITIONS_TOTAL.labels(transport="a2a", status=outcome).inc()

    async def _fail_output_limit(
        self,
        execution: TaskExecution,
        event_queue: EventQueue,
        error_code: str,
    ) -> None:
        """Persist a stable terminal failure for bounded A2A output."""
        from server.app.observability import A2A_LIMIT_REJECTIONS_TOTAL

        A2A_LIMIT_REJECTIONS_TOTAL.labels(direction="output", limit=error_code).inc()
        await self._runtime.transition(
            execution.task,
            execution.run,
            RunStatus.FAILED,
            reason="A2A output exceeded the configured resource limit",
            error_code=error_code,
        )
        await event_queue.enqueue_event(
            _status_event(
                execution,
                TaskState.TASK_STATE_FAILED,
                "A2A output exceeded the configured resource limit",
            )
        )

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
        state=cast(TaskState, state),
        text=text,
    )


def _artifact_event(
    execution: TaskExecution,
    event: ArtifactEvent,
) -> TaskArtifactUpdateEvent:
    if event.kind == "data":
        update = new_data_artifact_update_event(
            task_id=execution.task.id,
            context_id=execution.task.context_id,
            name=event.name,
            data=event.value,
            media_type=event.media_type,
            append=event.append,
            last_chunk=event.last_chunk,
            artifact_id=event.artifact_id,
        )
        update.artifact.extensions.extend(event.extensions)
        return update
    if event.kind == "raw":
        raw = event.value if isinstance(event.value, bytes) else str(event.value).encode()
        update = new_raw_artifact_update_event(
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
        update.artifact.extensions.extend(event.extensions)
        return update
    if event.kind == "url":
        update = new_url_artifact_update_event(
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
        update.artifact.extensions.extend(event.extensions)
        return update
    update = new_text_artifact_update_event(
        task_id=execution.task.id,
        context_id=execution.task.context_id,
        name=event.name,
        text=str(event.value),
        append=event.append,
        last_chunk=event.last_chunk,
        artifact_id=event.artifact_id,
    )
    update.artifact.extensions.extend(event.extensions)
    return update


def _artifact_size(kind: str, value: Any) -> int:
    """Return a stable byte estimate for output-limit enforcement."""
    if kind == "raw" and isinstance(value, bytes):
        return len(value)
    if kind == "data":
        return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return len(str(value).encode("utf-8"))


async def _with_flush_ticks(source: AsyncIterator[Any], interval: float) -> AsyncIterator[Any]:
    """Yield source events plus periodic ticks without cancelling the producer."""
    done_marker = object()
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await queue.put(exc)
        finally:
            await queue.put(done_marker)

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=interval)
            except TimeoutError:
                yield _FLUSH_TICK
                continue
            if event is done_marker:
                return
            if isinstance(event, BaseException):
                raise event
            yield event
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
