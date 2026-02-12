"""Internal protocol for server ↔ agent container communication.

This module defines the message formats used for communication between
the Cognition server (control plane) and agent runtime containers.

Messages are JSON-serialized and sent over WebSocket connections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# =============================================================================
# Server → Agent Messages (Control Messages)
# =============================================================================


@dataclass
class AgentStartMessage:
    """Initialize the agent with session context and configuration.

    Sent by the server when the agent container starts. The agent should
    initialize its LangGraph runtime with this configuration.
    """

    type: Literal["agent_start"] = "agent_start"
    session_id: str = ""
    project_id: str = ""
    workspace_path: str = "/workspace/repo"
    # LLM configuration
    llm_provider: str = "openai"  # openai, anthropic, bedrock
    llm_model: str = ""
    llm_temperature: float = 0.7
    # Agent behavior
    system_prompt: str | None = None
    max_iterations: int = 50
    # Optional: restore previous conversation state
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class UserMessage:
    """A user message for the agent to process.

    This triggers a new agent turn. The agent should process this message
    and stream events back to the server.
    """

    type: Literal["user_message"] = "user_message"
    session_id: str = ""
    content: str = ""
    turn_number: int = 0


@dataclass
class CancelMessage:
    """Cancel the current agent turn.

    The agent should interrupt the current LLM call or tool execution
    and return a partial result or cancellation acknowledgment.
    """

    type: Literal["cancel"] = "cancel"
    session_id: str = ""


@dataclass
class ShutdownMessage:
    """Gracefully shut down the agent.

    The agent should flush any pending state and exit cleanly.
    The server will disconnect after a timeout if the agent doesn't respond.
    """

    type: Literal["shutdown"] = "shutdown"
    session_id: str = ""


# Union type for all server → agent messages
ServerMessage = AgentStartMessage | UserMessage | CancelMessage | ShutdownMessage


# =============================================================================
# Agent → Server Events (Streaming Events)
# =============================================================================


@dataclass
class AgentReadyEvent:
    """Sent when the agent has initialized and is ready to process messages."""

    event: Literal["agent_ready"] = "agent_ready"
    session_id: str = ""


@dataclass
class AssistantMessageEvent:
    """Streaming text from the LLM assistant.

    The agent sends this as the LLM generates tokens. Multiple events
    may be sent for a single assistant response (streaming).
    """

    event: Literal["assistant_message"] = "assistant_message"
    session_id: str = ""
    content: str = ""
    is_complete: bool = False  # True when this is the final chunk


@dataclass
class ToolStartEvent:
    """A tool execution has started.

    Sent before any tool_output events for this tool call.
    """

    event: Literal["tool_start"] = "tool_start"
    session_id: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolOutputEvent:
    """Streaming output from a tool (stdout/stderr chunks).

    Multiple events may be sent for long-running tools (e.g., pytest).
    """

    event: Literal["tool_output"] = "tool_output"
    session_id: str = ""
    stream: Literal["stdout", "stderr"] = "stdout"
    content: str = ""


@dataclass
class ToolEndEvent:
    """A tool execution has completed.

    Sent after all tool_output events for this tool call.
    """

    event: Literal["tool_end"] = "tool_end"
    session_id: str = ""
    tool_name: str = ""
    exit_code: int = 0
    result_summary: str = ""  # Brief summary of the result


@dataclass
class ErrorEvent:
    """An error occurred during agent execution.

    This could be an LLM error, tool error, or internal error.
    """

    event: Literal["error"] = "error"
    session_id: str = ""
    message: str = ""
    error_type: str = ""  # e.g., "LLMError", "ToolError", "ValidationError"


@dataclass
class DoneEvent:
    """The agent turn has completed.

    Sent after all assistant messages and tool executions for this turn.
    The server can now accept the next user message.
    """

    event: Literal["done"] = "done"
    session_id: str = ""


# Union type for all agent → server events
AgentEvent = (
    AgentReadyEvent
    | AssistantMessageEvent
    | ToolStartEvent
    | ToolOutputEvent
    | ToolEndEvent
    | ErrorEvent
    | DoneEvent
)


# =============================================================================
# Serialization Helpers
# =============================================================================


def serialize_message(msg: ServerMessage | AgentEvent) -> str:
    """Serialize a message to JSON string."""
    import json

    # Convert dataclass to dict, filtering out None values
    def dict_factory(x: list[tuple[str, Any]]) -> dict[str, Any]:
        return {k: v for (k, v) in x if v is not None}

    return json.dumps(msg, default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o))


def parse_server_message(data: str) -> ServerMessage:
    """Parse a server message from JSON string."""
    import json

    obj = json.loads(data)
    msg_type = obj.get("type")

    if msg_type == "agent_start":
        return AgentStartMessage(**obj)
    elif msg_type == "user_message":
        return UserMessage(**obj)
    elif msg_type == "cancel":
        return CancelMessage(**obj)
    elif msg_type == "shutdown":
        return ShutdownMessage(**obj)
    else:
        raise ValueError(f"Unknown server message type: {msg_type}")


def parse_agent_event(data: str) -> AgentEvent:
    """Parse an agent event from JSON string."""
    import json

    obj = json.loads(data)
    event_type = obj.get("event")

    if event_type == "agent_ready":
        return AgentReadyEvent(**obj)
    elif event_type == "assistant_message":
        return AssistantMessageEvent(**obj)
    elif event_type == "tool_start":
        return ToolStartEvent(**obj)
    elif event_type == "tool_output":
        return ToolOutputEvent(**obj)
    elif event_type == "tool_end":
        return ToolEndEvent(**obj)
    elif event_type == "error":
        return ErrorEvent(**obj)
    elif event_type == "done":
        return DoneEvent(**obj)
    else:
        raise ValueError(f"Unknown agent event type: {event_type}")
