"""Cognition A2A Executor.

Implements the a2a-sdk AgentExecutor interface. Bridges A2A protocol
requests into Cognition's existing session/run/event model by reusing
the existing agent_event_stream() function from the messages route.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from a2a.helpers.proto_helpers import (
    new_task,
    new_text_artifact_update_event,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState

from server.app.protocols.a2a.mapping import (
    _RUN_STATUS_TO_A2A,
    extract_text_from_parts,
)

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.settings import Settings
    from server.app.storage.backend import StorageBackend

logger = structlog.get_logger(__name__)

# Canonical A2A artifact name for the agent's text response
_ARTIFACT_NAME = "response"


class CognitionA2AExecutor(AgentExecutor):
    """Bridges A2A protocol requests into Cognition's runtime.

    Uses Cognition's existing session model, agent_event_stream(),
    and event types. Each A2A message creates a new Cognition run
    within the session identified by the A2A contextId.

    The agent_name parameter determines which Cognition agent is used
    for sessions created by this executor. Each per-agent A2A endpoint
    gets its own executor with the correct agent_name.
    """

    def __init__(
        self,
        settings: Settings,
        session_agent_manager: SessionAgentManager,
        store: StorageBackend,
        agent_name: str = "default",
    ) -> None:
        self._settings = settings
        self._agent_manager = session_agent_manager
        self._store = store
        self._agent_name = agent_name

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        from server.app.agent.runtime import (
            DoneEvent,
            ErrorEvent,
            RunStateEvent,
            TokenEvent,
        )

        # 1. Extract user text from A2A message parts
        user_text = ""
        if context.message:
            user_text = extract_text_from_parts(context.message.parts)
        if not user_text:
            user_text = "(empty message)"

        # 2. Resolve scope from request metadata/context
        scope: dict[str, str] | None = None
        if context.call_context and hasattr(context.call_context, "headers"):
            headers = context.call_context.headers or {}
            scope_keys = self._settings.scope_keys
            if scope_keys:
                scope = {}
                for key in scope_keys:
                    header_name = f"x-cognition-scope-{key.replace('_', '-')}"
                    val = headers.get(header_name)
                    if val:
                        scope[key] = val

        # 3. Map contextId -> session_id; create session if needed
        context_id = context.context_id or str(uuid.uuid4())
        session_id = context_id
        session = await self._store.get_session(session_id)
        if not session:
            from server.app.models import SessionConfig

            thread_id = str(uuid.uuid4())
            workspace_path = str(self._settings.workspace_path)
            session = await self._store.create_session(
                session_id=session_id,
                thread_id=thread_id,
                config=SessionConfig(),
                title=f"A2A session {context_id[:8]}",
                scopes=scope,
                agent_name=self._agent_name,
                metadata={"a2a_context_id": context_id},
                workspace_path=workspace_path,
            )
            self._agent_manager.register_session(session_id, workspace_path)

        # 4. Get or create agent service
        service = self._agent_manager.get_service(session_id)
        if not service:
            workspace_path = session.workspace_path or str(self._settings.workspace_path)
            service = self._agent_manager.register_session(session_id, workspace_path)

        # 5. Create or reuse task
        task_id = context.task_id or str(uuid.uuid4())
        task = context.current_task
        if not task:
            task = new_task(
                task_id=task_id,
                context_id=context_id,
                state=TaskState.TASK_STATE_SUBMITTED,
            )
            await event_queue.enqueue_event(task)

        # 6. Mark working
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=task_id,
                context_id=context_id,
                state=TaskState.TASK_STATE_WORKING,
                text="Processing...",
            )
        )

        # 7. Stream Cognition events via service.stream_response()
        accumulated_text: list[str] = []
        workspace_path = session.workspace_path or str(self._settings.workspace_path)
        system_prompt = None
        if session.config and session.config.system_prompt:
            system_prompt = session.config.system_prompt

        try:
            async for event in service.stream_response(
                session_id=session_id,
                thread_id=session.thread_id,
                project_path=workspace_path,
                content=user_text,
                system_prompt=system_prompt,
                manager=self._agent_manager,
                scope=scope,
            ):
                if isinstance(event, TokenEvent):
                    accumulated_text.append(event.content)

                if isinstance(event, RunStateEvent):
                    a2a_state = _RUN_STATUS_TO_A2A.get(event.to_status)
                    if a2a_state == TaskState.TASK_STATE_INPUT_REQUIRED:
                        await event_queue.enqueue_event(
                            new_text_status_update_event(
                                task_id=task_id,
                                context_id=context_id,
                                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                                text="Waiting for approval",
                            )
                        )
                        return
                    if event.to_status == "done":
                        await _emit_final_artifact(
                            event_queue, task_id, context_id,
                            "".join(accumulated_text),
                        )
                        await event_queue.enqueue_event(
                            new_text_status_update_event(
                                task_id=task_id,
                                context_id=context_id,
                                state=TaskState.TASK_STATE_COMPLETED,
                                text="Done",
                            )
                        )
                        return
                    if event.to_status == "failed":
                        await event_queue.enqueue_event(
                            new_text_status_update_event(
                                task_id=task_id,
                                context_id=context_id,
                                state=TaskState.TASK_STATE_FAILED,
                                text=event.reason or "Run failed",
                            )
                        )
                        return

                if isinstance(event, DoneEvent):
                    await _emit_final_artifact(
                        event_queue, task_id, context_id,
                        "".join(accumulated_text),
                    )
                    await event_queue.enqueue_event(
                        new_text_status_update_event(
                            task_id=task_id,
                            context_id=context_id,
                            state=TaskState.TASK_STATE_COMPLETED,
                            text="Done",
                        )
                    )
                    return

                if isinstance(event, ErrorEvent):
                    await event_queue.enqueue_event(
                        new_text_status_update_event(
                            task_id=task_id,
                            context_id=context_id,
                            state=TaskState.TASK_STATE_FAILED,
                            text=event.message or "Agent error",
                        )
                    )
                    return

            # Stream exhausted without terminal event
            await _emit_final_artifact(
                event_queue, task_id, context_id,
                "".join(accumulated_text),
            )
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task_id=task_id,
                    context_id=context_id,
                    state=TaskState.TASK_STATE_COMPLETED,
                    text="Done",
                )
            )

        except Exception as e:
            logger.error("A2A executor error", error=str(e), task_id=task_id)
            await event_queue.enqueue_event(
                new_text_status_update_event(
                    task_id=task_id,
                    context_id=context_id,
                    state=TaskState.TASK_STATE_FAILED,
                    text=f"Error: {e}",
                )
            )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        # v0.10.0: cancel not supported
        raise NotImplementedError("Cancel not supported in v0.10.0")


async def _emit_final_artifact(
    event_queue: EventQueue,
    task_id: str,
    context_id: str,
    text: str,
) -> None:
    """Emit the accumulated agent response as an A2A artifact."""
    if not text:
        text = "(no response)"
    await event_queue.enqueue_event(
        new_text_artifact_update_event(
            task_id=task_id,
            context_id=context_id,
            name=_ARTIFACT_NAME,
            text=text,
            last_chunk=True,
        )
    )
