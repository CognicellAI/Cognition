"""Server to client event models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionStarted(BaseModel):
    """Event sent when a session is created."""

    event: Literal["session_started"] = "session_started"
    session_id: str
    network_mode: Literal["OFF", "ON"]
    workspace_path: str


class AssistantMessage(BaseModel):
    """Event sent when the assistant responds."""

    event: Literal["assistant_message"] = "assistant_message"
    session_id: str
    content: str


class ToolStart(BaseModel):
    """Event sent when a tool execution starts."""

    event: Literal["tool_start"] = "tool_start"
    session_id: str
    tool: str
    input: dict[str, Any]


class ToolOutput(BaseModel):
    """Event sent when a tool outputs data."""

    event: Literal["tool_output"] = "tool_output"
    session_id: str
    stream: Literal["stdout", "stderr"]
    chunk: str


class ToolEnd(BaseModel):
    """Event sent when a tool execution ends."""

    event: Literal["tool_end"] = "tool_end"
    session_id: str
    tool: str
    exit_code: int
    artifacts: dict[str, Any] = Field(default_factory=dict)


class DiffApplied(BaseModel):
    """Event sent when a diff is applied."""

    event: Literal["diff_applied"] = "diff_applied"
    session_id: str
    files_changed: list[str]
    diff_preview: str


class TestsFinished(BaseModel):
    """Event sent when tests finish running."""

    event: Literal["tests_finished"] = "tests_finished"
    session_id: str
    exit_code: int
    summary: str = ""


class Error(BaseModel):
    """Event sent when an error occurs."""

    event: Literal["error"] = "error"
    session_id: str | None = None
    message: str
    code: str = "UNKNOWN_ERROR"


class Done(BaseModel):
    """Event sent when a task is complete."""

    event: Literal["done"] = "done"
    session_id: str


ServerEvent = (
    SessionStarted
    | AssistantMessage
    | ToolStart
    | ToolOutput
    | ToolEnd
    | DiffApplied
    | TestsFinished
    | Error
    | Done
)
