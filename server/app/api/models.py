"""Pydantic models for REST API.

API request/response models using Pydantic.
These wrap the core domain models from server.app.models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from server.app.models import Session as CoreSession


# ============================================================================
# Session Models
# ============================================================================


class SessionCreate(BaseModel):
    """Request to create a new session.

    Server uses global settings exclusively - no per-session config.
    """

    title: Optional[str] = Field(None, max_length=200, description="Optional session title")


class SessionResponse(BaseModel):
    """Session information response."""

    id: str = Field(..., description="Unique session identifier")
    title: Optional[str] = Field(None, description="Session title")
    thread_id: str = Field(..., description="LangGraph thread ID for checkpointing")
    status: Literal["active", "inactive", "error"] = Field(..., description="Session status")
    created_at: str = Field(..., description="Session creation timestamp (ISO format)")
    updated_at: str = Field(..., description="Last activity timestamp (ISO format)")
    message_count: int = Field(0, description="Number of messages in session")

    @classmethod
    def from_core(cls, session: CoreSession) -> "SessionResponse":
        """Create from core domain model."""
        return cls(
            id=session.id,
            title=session.title,
            thread_id=session.thread_id,
            status=session.status.value,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=session.message_count,
        )


class SessionList(BaseModel):
    """List of sessions response."""

    sessions: list[SessionResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of sessions")


class SessionUpdate(BaseModel):
    """Request to update a session.

    Allows updating metadata and LLM configuration.
    """

    title: Optional[str] = Field(None, max_length=200)
    config: Optional[SessionConfig] = Field(None, description="Update LLM configuration")


# ============================================================================
# Message Models
# ============================================================================


class MessageCreate(BaseModel):
    """Request to send a message."""

    content: str = Field(..., min_length=1, description="Message content")
    parent_id: Optional[str] = Field(None, description="ID of parent message for threading")
    model: Optional[str] = Field(
        None,
        description="Model to use for this message (e.g., 'gpt-4o', 'claude-3-sonnet'). Uses server default if not specified.",
    )


class MessageResponse(BaseModel):
    """Message information response."""

    id: str = Field(..., description="Unique message identifier")
    session_id: str = Field(..., description="Associated session ID")
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role")
    content: Optional[str] = Field(None, description="Message content (if complete)")
    parent_id: Optional[str] = Field(None, description="Parent message ID")
    model: Optional[str] = Field(None, description="Model used for this message")
    created_at: datetime = Field(..., description="Message creation timestamp")


class MessageList(BaseModel):
    """List of messages response."""

    messages: list[MessageResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of messages")
    has_more: bool = Field(False, description="Whether more messages exist")


# ============================================================================
# SSE Event Models
# ============================================================================


class TokenEvent(BaseModel):
    """Server-sent event: Token streaming."""

    event: Literal["token"] = "token"
    data: dict = Field(..., description="Token data with 'content' field")


class ToolCallEvent(BaseModel):
    """Server-sent event: Tool invocation."""

    event: Literal["tool_call"] = "tool_call"
    data: dict = Field(..., description="Tool call with 'name', 'args', 'id'")


class ToolResultEvent(BaseModel):
    """Server-sent event: Tool execution result."""

    event: Literal["tool_result"] = "tool_result"
    data: dict = Field(..., description="Tool result with 'tool_call_id', 'output', 'exit_code'")


class ErrorEvent(BaseModel):
    """Server-sent event: Error occurred."""

    event: Literal["error"] = "error"
    data: dict = Field(..., description="Error with 'message' and optional 'code'")


class DoneEvent(BaseModel):
    """Server-sent event: Stream complete."""

    event: Literal["done"] = "done"
    data: dict = Field(default_factory=dict)


class UsageEvent(BaseModel):
    """Server-sent event: Token usage update."""

    event: Literal["usage"] = "usage"
    data: dict = Field(
        ..., description="Usage with 'input_tokens', 'output_tokens', 'estimated_cost'"
    )


# ============================================================================
# Health & Status Models
# ============================================================================


class HealthStatus(BaseModel):
    """Health check response."""

    status: Literal["healthy", "unhealthy"] = Field(..., description="Overall health status")
    version: str = Field(..., description="Server version")
    active_sessions: int = Field(..., description="Number of active sessions")
    timestamp: datetime = Field(..., description="Health check timestamp")


class ReadyStatus(BaseModel):
    """Readiness probe response."""

    ready: bool = Field(..., description="Whether server is ready to accept requests")


# ============================================================================
# Error Models
# ============================================================================


class ErrorResponse(BaseModel):
    """Error response."""

    error: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code")
    details: Optional[dict[str, Any]] = Field(None, description="Additional error details")


# ============================================================================
# Config Models
# ============================================================================


class ConfigResponse(BaseModel):
    """Server configuration response."""

    server: dict = Field(..., description="Server configuration")
    llm: dict = Field(..., description="LLM default configuration")
    rate_limit: dict = Field(..., description="Rate limiting configuration")


class ProviderInfo(BaseModel):
    """Information about an available LLM provider."""

    id: str = Field(..., description="Provider identifier")
    name: str = Field(..., description="Display name")
    models: list[str] = Field(default_factory=list, description="Available models")


class ProviderList(BaseModel):
    """List of available providers and models."""

    providers: list[ProviderInfo] = Field(default_factory=list)
    default_provider: Optional[str] = Field(None, description="Default provider ID")
    default_model: Optional[str] = Field(None, description="Default model ID")
