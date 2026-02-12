"""Unit tests for exceptions module."""

from __future__ import annotations

import pytest

from server.app.exceptions import (
    CognitionError,
    ErrorCode,
    LLMRateLimitError,
    LLMUnavailableError,
    ProjectNotFoundError,
    RateLimitError,
    SessionLimitExceededError,
    SessionNotFoundError,
    ToolExecutionError,
    ValidationError,
)


class TestCognitionError:
    """Test base CognitionError class."""

    def test_basic_error(self):
        """Test creating a basic error."""
        error = CognitionError("Something went wrong")
        assert error.message == "Something went wrong"
        assert error.code == ErrorCode.INTERNAL_ERROR
        assert error.details == {}

    def test_error_with_code(self):
        """Test error with specific code."""
        error = CognitionError("Not found", code=ErrorCode.NOT_FOUND, details={"id": "123"})
        assert error.code == ErrorCode.NOT_FOUND
        assert error.details == {"id": "123"}

    def test_to_dict(self):
        """Test converting error to dictionary."""
        error = CognitionError(
            "Test error", code=ErrorCode.VALIDATION_ERROR, details={"field": "name"}
        )
        result = error.to_dict()
        assert result["error"] is True
        assert result["code"] == ErrorCode.VALIDATION_ERROR
        assert result["message"] == "Test error"
        assert result["details"] == {"field": "name"}

    def test_error_is_exception(self):
        """Test that error can be raised and caught."""
        with pytest.raises(CognitionError) as exc_info:
            raise CognitionError("Test error")
        assert str(exc_info.value) == "Test error"


class TestSessionErrors:
    """Test session-related errors."""

    def test_session_not_found_error(self):
        """Test SessionNotFoundError."""
        error = SessionNotFoundError("session-123")
        assert error.code == ErrorCode.SESSION_NOT_FOUND
        assert "session-123" in error.message
        assert error.details == {"session_id": "session-123"}

    def test_session_limit_exceeded_error(self):
        """Test SessionLimitExceededError."""
        error = SessionLimitExceededError(100)
        assert error.code == ErrorCode.SESSION_LIMIT_EXCEEDED
        assert "100" in error.message
        assert error.details == {"max_sessions": 100}


class TestLLMErrors:
    """Test LLM-related errors."""

    def test_llm_unavailable_error(self):
        """Test LLMUnavailableError."""
        error = LLMUnavailableError("openai", "API key invalid")
        assert error.code == ErrorCode.LLM_UNAVAILABLE
        assert "openai" in error.message
        assert error.details == {"provider": "openai", "reason": "API key invalid"}

    def test_llm_unavailable_error_without_reason(self):
        """Test LLMUnavailableError without reason."""
        error = LLMUnavailableError("bedrock")
        assert error.details["reason"] is None

    def test_llm_rate_limit_error(self):
        """Test LLMRateLimitError."""
        error = LLMRateLimitError("openai", retry_after=60)
        assert error.code == ErrorCode.LLM_RATE_LIMIT
        assert error.details == {"provider": "openai", "retry_after": 60}

    def test_llm_rate_limit_error_without_retry(self):
        """Test LLMRateLimitError without retry_after."""
        error = LLMRateLimitError("bedrock")
        assert error.details["retry_after"] is None


class TestToolErrors:
    """Test tool-related errors."""

    def test_tool_execution_error(self):
        """Test ToolExecutionError."""
        error = ToolExecutionError("git_status", 1, "fatal: not a git repository")
        assert error.code == ErrorCode.TOOL_EXECUTION_FAILED
        assert "git_status" in error.message
        assert "exit code 1" in error.message
        assert error.details == {
            "tool_name": "git_status",
            "exit_code": 1,
            "output": "fatal: not a git repository",
        }


class TestProjectErrors:
    """Test project-related errors."""

    def test_project_not_found_error(self):
        """Test ProjectNotFoundError."""
        error = ProjectNotFoundError("proj-123")
        assert error.code == ErrorCode.PROJECT_NOT_FOUND
        assert "proj-123" in error.message
        assert error.details == {"project_id": "proj-123"}


class TestValidationError:
    """Test validation errors."""

    def test_validation_error(self):
        """Test ValidationError."""
        error = ValidationError("email", "Invalid email format")
        assert error.code == ErrorCode.VALIDATION_ERROR
        assert "email" in error.message
        assert "Invalid email format" in error.message
        assert error.details == {"field": "email", "error": "Invalid email format"}


class TestRateLimitError:
    """Test rate limit errors."""

    def test_rate_limit_error(self):
        """Test RateLimitError."""
        error = RateLimitError("api_calls", 100, 60)
        assert error.code == ErrorCode.RATE_LIMITED
        assert "api_calls" in error.message
        assert error.details == {
            "resource": "api_calls",
            "limit": 100,
            "window_seconds": 60,
        }


class TestErrorCodeEnum:
    """Test ErrorCode enum values."""

    def test_all_error_codes_are_strings(self):
        """Test that all error codes are string values."""
        for code in ErrorCode:
            assert isinstance(code.value, str)
            assert len(code.value) > 0

    def test_error_code_uniqueness(self):
        """Test that all error codes are unique."""
        values = [code.value for code in ErrorCode]
        assert len(values) == len(set(values))
