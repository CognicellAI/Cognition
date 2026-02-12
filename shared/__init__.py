"""Shared protocol definitions for client-server communication."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """WebSocket message types."""

    # Client → Server
    CREATE_PROJECT = "create_project"
    CREATE_SESSION = "create_session"
    RESUME_SESSION = "resume_session"
    USER_MESSAGE = "user_message"
    DISCONNECT = "disconnect"

    # Server → Client
    PROJECT_CREATED = "project_created"
    SESSION_STARTED = "session_started"
    SESSION_RESUMED = "session_resumed"
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


class BaseMessage(BaseModel):
    """Base class for all messages."""

    msg_type: MessageType = Field(..., alias="type")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True


# Client → Server Messages


class CreateProject(BaseMessage):
    """Create a new project."""

    msg_type: Literal[MessageType.CREATE_PROJECT] = Field(MessageType.CREATE_PROJECT, alias="type")
    user_prefix: Optional[str] = None
    project_path: Optional[str] = None


class CreateSession(BaseMessage):
    """Create a new session for a project."""

    msg_type: Literal[MessageType.CREATE_SESSION] = Field(MessageType.CREATE_SESSION, alias="type")
    project_id: str


class ResumeSession(BaseMessage):
    """Resume an existing session."""

    msg_type: Literal[MessageType.RESUME_SESSION] = Field(MessageType.RESUME_SESSION, alias="type")
    session_id: str
    thread_id: str


class UserMessage(BaseMessage):
    """User message to the agent."""

    msg_type: Literal[MessageType.USER_MESSAGE] = Field(MessageType.USER_MESSAGE, alias="type")
    session_id: str
    content: str


class Disconnect(BaseMessage):
    """Client disconnecting."""

    msg_type: Literal[MessageType.DISCONNECT] = Field(MessageType.DISCONNECT, alias="type")
    session_id: str


# Server → Client Messages


class ProjectCreated(BaseMessage):
    """Project created successfully."""

    msg_type: Literal[MessageType.PROJECT_CREATED] = Field(
        MessageType.PROJECT_CREATED, alias="type"
    )
    project_id: str
    project_path: str


class SessionStarted(BaseMessage):
    """Session started successfully."""

    msg_type: Literal[MessageType.SESSION_STARTED] = Field(
        MessageType.SESSION_STARTED, alias="type"
    )
    session_id: str
    thread_id: str
    project_id: str


class SessionResumed(BaseMessage):
    """Session resumed successfully."""

    msg_type: Literal[MessageType.SESSION_RESUMED] = Field(
        MessageType.SESSION_RESUMED, alias="type"
    )
    session_id: str
    thread_id: str


class Token(BaseMessage):
    """LLM token (streaming)."""

    msg_type: Literal[MessageType.TOKEN] = Field(MessageType.TOKEN, alias="type")
    content: str


class ToolCall(BaseMessage):
    """Agent is calling a tool."""

    msg_type: Literal[MessageType.TOOL_CALL] = Field(MessageType.TOOL_CALL, alias="type")
    name: str
    args: dict[str, Any]
    id: str


class ToolResult(BaseMessage):
    """Tool execution result."""

    msg_type: Literal[MessageType.TOOL_RESULT] = Field(MessageType.TOOL_RESULT, alias="type")
    tool_call_id: str
    output: str
    exit_code: int = 0


class Done(BaseMessage):
    """Agent finished processing."""

    msg_type: Literal[MessageType.DONE] = Field(MessageType.DONE, alias="type")


class Error(BaseMessage):
    """Error occurred."""

    msg_type: Literal[MessageType.ERROR] = Field(MessageType.ERROR, alias="type")
    message: str
    code: Optional[str] = None


# Union type for all messages
Message = (
    CreateProject
    | CreateSession
    | ResumeSession
    | UserMessage
    | Disconnect
    | ProjectCreated
    | SessionStarted
    | SessionResumed
    | Token
    | ToolCall
    | ToolResult
    | Done
    | Error
)


def parse_message(data: str | dict) -> Message:
    """Parse a message from JSON string or dict.

    Args:
        data: JSON string or dict to parse.

    Returns:
        Parsed message object.

    Raises:
        ValueError: If message type is unknown.
    """
    if isinstance(data, str):
        data_dict: dict = json.loads(data)
    else:
        data_dict = data

    msg_type = data_dict.get("type")
    if msg_type is None:
        raise ValueError("Message missing 'type' field")

    type_map = {
        MessageType.CREATE_PROJECT: CreateProject,
        MessageType.CREATE_SESSION: CreateSession,
        MessageType.RESUME_SESSION: ResumeSession,
        MessageType.USER_MESSAGE: UserMessage,
        MessageType.DISCONNECT: Disconnect,
        MessageType.PROJECT_CREATED: ProjectCreated,
        MessageType.SESSION_STARTED: SessionStarted,
        MessageType.SESSION_RESUMED: SessionResumed,
        MessageType.TOKEN: Token,
        MessageType.TOOL_CALL: ToolCall,
        MessageType.TOOL_RESULT: ToolResult,
        MessageType.DONE: Done,
        MessageType.ERROR: Error,
    }

    try:
        msg_type_enum = MessageType(msg_type)
    except ValueError:
        raise ValueError(f"Unknown message type: {msg_type}")

    if msg_type_enum not in type_map:
        raise ValueError(f"Unknown message type: {msg_type}")

    return type_map[msg_type_enum](**data_dict)


def message_to_json(message: Message) -> str:
    """Convert a message to JSON string.

    Args:
        message: Message to serialize.

    Returns:
        JSON string.
    """
    return message.model_dump_json()
