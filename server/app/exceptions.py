"""Centralized error handling and custom exceptions for Cognition."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Standardized error codes for client communication."""

    # General errors
    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"

    # Session errors
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_EXPIRED = "session_expired"
    SESSION_LIMIT_EXCEEDED = "session_limit_exceeded"

    # LLM errors
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_TIMEOUT = "llm_timeout"
    LLM_INVALID_RESPONSE = "llm_invalid_response"

    # Tool errors
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_TIMEOUT = "tool_timeout"

    # Project errors
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_ALREADY_EXISTS = "project_already_exists"
    PROJECT_PATH_INVALID = "project_path_invalid"


class CognitionError(Exception):
    """Base exception for all Cognition errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for JSON serialization."""
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class SessionError(CognitionError):
    """Errors related to session management."""

    pass


class SessionNotFoundError(SessionError):
    """Session not found."""

    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session not found: {session_id}",
            code=ErrorCode.SESSION_NOT_FOUND,
            details={"session_id": session_id},
        )


class SessionLimitExceededError(SessionError):
    """Maximum number of sessions exceeded."""

    def __init__(self, max_sessions: int):
        super().__init__(
            message=f"Maximum sessions ({max_sessions}) exceeded",
            code=ErrorCode.SESSION_LIMIT_EXCEEDED,
            details={"max_sessions": max_sessions},
        )


class LLMError(CognitionError):
    """Errors related to LLM operations."""

    pass


class LLMUnavailableError(LLMError):
    """LLM service is unavailable."""

    def __init__(self, provider: str, reason: str | None = None):
        super().__init__(
            message=f"LLM service '{provider}' is unavailable",
            code=ErrorCode.LLM_UNAVAILABLE,
            details={"provider": provider, "reason": reason},
        )


class LLMRateLimitError(LLMError):
    """LLM API rate limit hit."""

    def __init__(self, provider: str, retry_after: int | None = None):
        super().__init__(
            message=f"LLM rate limit exceeded for '{provider}'",
            code=ErrorCode.LLM_RATE_LIMIT,
            details={"provider": provider, "retry_after": retry_after},
        )


class ToolError(CognitionError):
    """Errors related to tool execution."""

    pass


class ToolExecutionError(ToolError):
    """Tool execution failed."""

    def __init__(self, tool_name: str, exit_code: int, output: str):
        super().__init__(
            message=f"Tool '{tool_name}' failed with exit code {exit_code}",
            code=ErrorCode.TOOL_EXECUTION_FAILED,
            details={"tool_name": tool_name, "exit_code": exit_code, "output": output},
        )


class ProjectError(CognitionError):
    """Errors related to project management."""

    pass


class ProjectNotFoundError(ProjectError):
    """Project not found."""

    def __init__(self, project_id: str):
        super().__init__(
            message=f"Project not found: {project_id}",
            code=ErrorCode.PROJECT_NOT_FOUND,
            details={"project_id": project_id},
        )


class ValidationError(CognitionError):
    """Input validation failed."""

    def __init__(self, field: str, message: str):
        super().__init__(
            message=f"Validation failed for '{field}': {message}",
            code=ErrorCode.VALIDATION_ERROR,
            details={"field": field, "error": message},
        )


class RateLimitError(CognitionError):
    """Rate limit exceeded."""

    def __init__(self, resource: str, limit: int, window: int):
        super().__init__(
            message=f"Rate limit exceeded for {resource}",
            code=ErrorCode.RATE_LIMITED,
            details={"resource": resource, "limit": limit, "window_seconds": window},
        )
