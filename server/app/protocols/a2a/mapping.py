"""Cognition runtime events to A2A protocol event translation.

Maps Cognition's canonical StreamEvent types to a2a-sdk TaskStatusUpdateEvent
and TaskArtifactUpdateEvent objects. Translates run lifecycle states to A2A
TaskState values.
"""

from __future__ import annotations

import uuid

import structlog
from a2a.types import TaskState

from server.app.agent.runtime import (
    DoneEvent,
    ErrorEvent,
    RunStateEvent,
    StreamEvent,
)

logger = structlog.get_logger(__name__)

# Cognition run_status → A2A TaskState
_RUN_STATUS_TO_A2A: dict[str, int] = {
    "queued": TaskState.TASK_STATE_SUBMITTED,
    "starting": TaskState.TASK_STATE_WORKING,
    "active": TaskState.TASK_STATE_WORKING,
    "idle": TaskState.TASK_STATE_COMPLETED,
    "waiting_for_approval": TaskState.TASK_STATE_INPUT_REQUIRED,
    "interrupted": TaskState.TASK_STATE_INPUT_REQUIRED,
    "stalled": TaskState.TASK_STATE_WORKING,
    "done": TaskState.TASK_STATE_COMPLETED,
    "failed": TaskState.TASK_STATE_FAILED,
    "rejected": TaskState.TASK_STATE_REJECTED,
    "aborted": TaskState.TASK_STATE_CANCELED,
    "aborting": TaskState.TASK_STATE_WORKING,
    "expired": TaskState.TASK_STATE_FAILED,
}


def is_terminal_state(a2a_state: int) -> bool:
    return a2a_state in {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }


def event_to_a2a_state(event: StreamEvent) -> int | None:
    """Return A2A TaskState if the event triggers a state transition, else None."""
    if isinstance(event, RunStateEvent):
        return _RUN_STATUS_TO_A2A.get(event.to_status)
    if isinstance(event, DoneEvent):
        return int(TaskState.TASK_STATE_COMPLETED)
    if isinstance(event, ErrorEvent):
        return int(TaskState.TASK_STATE_FAILED)
    return None


def is_hitl_pause(event: StreamEvent) -> bool:
    return isinstance(event, RunStateEvent) and event.to_status == "waiting_for_approval"


def build_task_id() -> str:
    return str(uuid.uuid4())
