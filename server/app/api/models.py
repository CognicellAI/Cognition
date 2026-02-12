"""Pydantic models for REST API.

All request/response models for the REST API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Project Models
# ============================================================================


class ProjectCreate(BaseModel):
    """Request to create a new project."""

    name: str = Field(..., min_length=1, max_length=100, description="Project name")
    description: Optional[str] = Field(None, max_length=500, description="Project description")
    path: Optional[str] = Field(None, description="Custom path for project workspace")


class ProjectResponse(BaseModel):
    """Project information response."""

    id: str = Field(..., description="Unique project identifier")
    name: str = Field(..., description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    path: str = Field(..., description="Absolute path to project workspace")
    created_at: datetime = Field(..., description="Project creation timestamp")
    updated_at: datetime = Field(..., description="Last modification timestamp")


class ProjectList(BaseModel):
    """List of projects response."""

    projects: list[ProjectResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of projects")


# ============================================================================
# Session Models
# ============================================================================


class SessionCreate(BaseModel):
    """Request to create a new session."""

    project_id: str = Field(..., description="ID of the project for this session")
    title: Optional[str] = Field(None, max_length=200, description="Optional session title")
    config: Optional[SessionConfig] = Field(None, description="Session configuration")


class SessionConfig(BaseModel):
    """Session configuration options."""

    provider: Optional[Literal["openai", "bedrock", "mock", "openai_compatible"]] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    system_prompt: Optional[str] = None


class SessionResponse(BaseModel):
    """Session information response."""

    id: str = Field(..., description="Unique session identifier")
    project_id: str = Field(..., description="Associated project ID")
    title: Optional[str] = Field(None, description="Session title")
    thread_id: str = Field(..., description="LangGraph thread ID for checkpointing")
    status: Literal["active", "inactive", "error"] = Field(..., description="Session status")
    config: SessionConfig = Field(..., description="Current session configuration")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime = Field(..., description="Last activity timestamp")
    message_count: int = Field(0, description="Number of messages in session")


class SessionList(BaseModel):
    """List of sessions response."""

    sessions: list[SessionResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of sessions")


class SessionUpdate(BaseModel):
    """Request to update a session."""

    title: Optional[str] = Field(None, max_length=200)
    config: Optional[SessionConfig] = None


# ============================================================================
# Message Models
# ============================================================================


class MessageCreate(BaseModel):
    """Request to send a message."""

    content: str = Field(..., min_length=1, description="Message content")
    parent_id: Optional[str] = Field(None, description="ID of parent message for threading")


class MessageResponse(BaseModel):
    """Message information response."""

    id: str = Field(..., description="Unique message identifier")
    session_id: str = Field(..., description="Associated session ID")
    role: Literal["user", "assistant", "system"] = Field(..., description="Message role")
    content: Optional[str] = Field(None, description="Message content (if complete)")
    parent_id: Optional[str] = Field(None, description="Parent message ID")
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


class ConfigUpdate(BaseModel):
    """Configuration update request."""

    llm: Optional[dict] = None
    rate_limit: Optional[dict] = None
