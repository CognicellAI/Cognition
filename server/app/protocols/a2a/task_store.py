"""Durable A2A TaskStore projection over Cognition's neutral task runtime."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import TYPE_CHECKING, Any

from a2a.helpers.proto_helpers import (
    new_artifact,
    new_data_part,
    new_raw_part,
    new_text_artifact,
    new_text_message,
    new_text_part,
    new_url_part,
)
from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskStore
from a2a.types import (
    ListTasksRequest,
    ListTasksResponse,
    Part,
    Role,
    Task,
    TaskState,
)
from a2a.types import (
    Message as A2AMessage,
)
from a2a.types import (
    TaskStatus as A2ATaskStatus,
)
from google.protobuf.timestamp_pb2 import Timestamp  # type: ignore[import-untyped]

from server.app.agent.task_runtime import AgentTaskRuntime, GetTask, ListTasks
from server.app.models import RuntimeTask, TaskStatus
from server.app.storage.backend import StorageBackend

if TYPE_CHECKING:
    from server.app.storage.artifact_store import ArtifactStore


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
_A2A_STATUS_TO_TASK = {value: key for key, value in _TASK_STATUS_TO_A2A.items()}


def effective_scope_from_context(context: ServerCallContext) -> dict[str, str]:
    """Read builder-authorized scope attached by trusted ingress."""
    raw_scope = context.state.get("effective_scope", {})
    if not isinstance(raw_scope, dict):
        return {}
    return {str(key): str(value) for key, value in raw_scope.items()}


class CognitionTaskStore(TaskStore):
    """Project the durable neutral task aggregate into A2A protobuf objects.

    SDK ``save`` calls do not create an independent task truth. The executor has
    already persisted each lifecycle transition through ``AgentTaskRuntime``;
    this adapter reconstructs A2A tasks from that durable state on every read.
    """

    def __init__(
        self,
        runtime: AgentTaskRuntime,
        store: StorageBackend,
        *,
        agent_name: str,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._agent_name = agent_name
        self._artifact_store = artifact_store

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Validate ownership; lifecycle persistence is owned by the runtime."""
        await self._runtime.get(
            GetTask(
                task_id=task.id,
                agent_name=self._agent_name,
                effective_scope=effective_scope_from_context(context),
            )
        )

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        """Get one scope- and agent-isolated task projection."""
        task = await self._runtime.get(
            GetTask(
                task_id=task_id,
                agent_name=self._agent_name,
                effective_scope=effective_scope_from_context(context),
            )
        )
        return await self.project(task) if task is not None else None

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """List isolated tasks using the A2A cursor and filter contract."""
        scope = effective_scope_from_context(context)
        statuses = None
        if params.status != TaskState.TASK_STATE_UNSPECIFIED:
            neutral_status = _A2A_STATUS_TO_TASK.get(params.status)
            statuses = {neutral_status} if neutral_status is not None else set()
        page_size = params.page_size if params.HasField("page_size") else 50
        page = await self._runtime.list(
            ListTasks(
                agent_name=self._agent_name,
                effective_scope=scope,
                context_id=params.context_id or None,
                statuses=statuses,
                limit=page_size,
                cursor=params.page_token or None,
            )
        )
        selected = [
            task
            for task in page.tasks
            if not params.HasField("status_timestamp_after")
            or _parse_datetime(task.updated_at)
            > params.status_timestamp_after.ToDatetime().astimezone()
        ]

        total_size = await self._count_tasks(
            scope=scope,
            context_id=params.context_id or None,
            statuses=statuses,
            updated_after=(
                params.status_timestamp_after.ToDatetime().astimezone()
                if params.HasField("status_timestamp_after")
                else None
            ),
        )
        return ListTasksResponse(
            tasks=[await self.project(task) for task in selected],
            next_page_token=page.next_cursor or "",
            page_size=page_size,
            total_size=total_size,
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Honor the SDK interface without exposing task deletion as A2A behavior."""
        await self.get(task_id, context)

    async def project(self, task: RuntimeTask) -> Task:
        """Project one already-authorized neutral task to its A2A representation."""
        messages = await self._store.list_messages_for_session(
            task.session_id,
            task.effective_scope,
        )
        history = []
        assistant_text = ""
        for message in messages:
            metadata = message.metadata or {}
            if metadata.get("task_id") != task.id or message.role not in {"user", "assistant"}:
                continue
            role = Role.ROLE_USER if message.role == "user" else Role.ROLE_AGENT
            projected = new_text_message(
                message.content or "",
                media_type="text/plain",
                context_id=task.context_id,
                task_id=task.id,
                role=role,
            )
            projected.message_id = message.id
            history.append(projected)
            if message.role == "assistant" and message.content:
                assistant_text = message.content

        artifacts = []
        descriptors = task.metadata.get("artifacts", [])
        if isinstance(descriptors, list):
            for descriptor in descriptors:
                if not isinstance(descriptor, dict):
                    continue
                parts = [
                    _project_artifact_part(part)
                    for part in descriptor.get("parts", [])
                    if isinstance(part, dict)
                ]
                if parts:
                    artifacts.append(
                        new_artifact(
                            parts=parts,
                            name=str(descriptor.get("name") or "artifact"),
                            description=descriptor.get("description"),
                            artifact_id=str(descriptor.get("artifact_id") or ""),
                        )
                    )
        if not artifacts and self._artifact_store is not None:
            artifact = await self._artifact_store.get_artifact(
                f"task-{task.id}-response",
                task.effective_scope,
            )
            if artifact is not None:
                artifacts.append(
                    new_text_artifact(
                        name=artifact.name,
                        text=artifact.content,
                        media_type=artifact.content_type,
                        artifact_id=artifact.id,
                    )
                )
        if not artifacts and assistant_text:
            artifacts.append(
                new_text_artifact(
                    name="response",
                    text=assistant_text,
                    media_type="text/plain",
                    artifact_id=f"task-{task.id}-response",
                )
            )

        status = A2ATaskStatus(
            state=_TASK_STATUS_TO_A2A[task.status]  # type: ignore[arg-type]
        )
        status.timestamp.CopyFrom(_timestamp(task.updated_at))
        if task.status_reason:
            status_message = new_text_message(
                task.status_reason,
                media_type="text/plain",
                context_id=task.context_id,
                task_id=task.id,
                role=Role.ROLE_AGENT,
            )
            # Projection must be deterministic so concurrent subscribers receive
            # byte-equivalent event payloads.
            status_message.message_id = f"task-{task.id}-status-{task.status.value}"
            status.message.CopyFrom(status_message)
        projected_task = Task(
            id=task.id,
            context_id=task.context_id,
            status=status,
            history=history,
            artifacts=artifacts,
        )
        projected_task.metadata.update(
            {
                "agentName": task.agent_name,
                "currentRunId": task.current_run_id or "",
                "lastRunId": task.last_run_id or "",
            }
        )
        return projected_task

    async def project_message(self, task: RuntimeTask) -> A2AMessage | None:
        """Project an idempotently persisted message-only interaction."""
        message_id = task.metadata.get("direct_message_id")
        if not isinstance(message_id, str):
            return None
        messages = await self._store.list_messages_for_session(
            task.session_id,
            task.effective_scope,
        )
        message = next((item for item in messages if item.id == message_id), None)
        if message is None:
            return None
        projected = new_text_message(
            message.content or "",
            media_type=str(task.metadata.get("direct_message_media_type") or "text/plain"),
            context_id=task.context_id,
            role=Role.ROLE_AGENT,
        )
        projected.message_id = message.id
        return projected

    async def _count_tasks(
        self,
        *,
        scope: dict[str, str],
        context_id: str | None,
        statuses: set[TaskStatus] | None,
        updated_after: datetime | None,
    ) -> int:
        total = 0
        cursor: str | None = None
        while True:
            page = await self._runtime.list(
                ListTasks(
                    agent_name=self._agent_name,
                    effective_scope=scope,
                    context_id=context_id,
                    statuses=statuses,
                    limit=100,
                    cursor=cursor,
                )
            )
            total += sum(
                1
                for task in page.tasks
                if updated_after is None or _parse_datetime(task.updated_at) > updated_after
            )
            cursor = page.next_cursor
            if cursor is None:
                return total


def _parse_datetime(value: str) -> datetime:
    """Parse Cognition's UTC ISO timestamp into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def _timestamp(value: str) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromDatetime(_parse_datetime(value))
    return timestamp


def _project_artifact_part(part: dict[str, Any]) -> Part:
    kind = part.get("kind")
    value = part.get("value")
    media_type = part.get("media_type")
    filename = part.get("filename")
    if kind == "data":
        return new_data_part(value, media_type=media_type)
    if kind == "raw":
        return new_raw_part(
            base64.b64decode(str(value)),
            media_type=media_type,
            filename=filename,
        )
    if kind == "url":
        return new_url_part(
            str(value),
            media_type=media_type,
            filename=filename,
        )
    return new_text_part(str(value), media_type=media_type)


__all__ = [
    "CognitionTaskStore",
    "effective_scope_from_context",
]
