"""Custom exception hierarchy for Cognition."""

from typing import Any


class CognitionError(Exception):
    """Base exception for all Cognition errors."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SessionError(CognitionError):
    """Errors related to session management."""

    pass


class SessionNotFoundError(SessionError):
    """Session not found."""

    pass


class SessionLimitError(SessionError):
    """Maximum number of sessions reached."""

    pass


class ContainerError(CognitionError):
    """Errors related to container execution."""

    pass


class ContainerNotFoundError(ContainerError):
    """Container not found."""

    pass


class ContainerExecutionError(ContainerError):
    """Error during container execution."""

    pass


class ContainerTimeoutError(ContainerError):
    """Container execution timed out."""

    pass


class ToolError(CognitionError):
    """Errors related to tool execution."""

    pass


class ToolValidationError(ToolError):
    """Tool request validation failed."""

    pass


class ToolExecutionError(ToolError):
    """Error during tool execution."""

    pass


class PathValidationError(ToolError):
    """Path validation failed (e.g., path traversal attempt)."""

    pass


class AgentError(CognitionError):
    """Errors related to agent runtime."""

    pass


class AgentRuntimeError(AgentError):
    """Error during agent execution."""

    pass


class ProtocolError(CognitionError):
    """Errors related to protocol/communication."""

    pass


class MessageValidationError(ProtocolError):
    """Message validation failed."""

    pass


class WebSocketError(ProtocolError):
    """WebSocket communication error."""

    pass
