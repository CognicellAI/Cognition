"""Pydantic models for REST API.

API request/response models using Pydantic.
These wrap the core domain models from server.app.models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field

from server.app.models import Session as CoreSession
from server.app.models import SessionConfig

# ============================================================================
# Session Models
# ============================================================================


class SessionCreate(BaseModel):
    """Request to create a new session.

    Server uses global settings exclusively - no per-session config.
    """

    title: str | None = Field(None, max_length=200, description="Optional session title")
    agent_name: str = Field("default", description="Agent to use for this session")
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Arbitrary key-value metadata attached to the session",
    )


class SessionResponse(BaseModel):
    """Session information response."""

    id: str = Field(..., description="Unique session identifier")
    title: str | None = Field(None, description="Session title")
    thread_id: str = Field(..., description="LangGraph thread ID for checkpointing")
    status: Literal["active", "inactive", "error", "waiting_for_approval"] = Field(
        ..., description="Session status"
    )
    created_at: str = Field(..., description="Session creation timestamp (ISO format)")
    updated_at: str = Field(..., description="Last activity timestamp (ISO format)")
    message_count: int = Field(0, description="Number of messages in session")
    agent_name: str = Field("default", description="Agent bound to this session")
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata attached to the session",
    )

    @classmethod
    def from_core(cls, session: CoreSession) -> SessionResponse:
        """Create from core domain model."""
        return cls(
            id=session.id,
            title=session.title,
            thread_id=session.thread_id,
            status=session.status.value,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=session.message_count,
            agent_name=session.agent_name,
            metadata=session.metadata,
        )


class SessionList(BaseModel):
    """List of sessions response."""

    sessions: list[SessionResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of sessions")


class SessionUpdate(BaseModel):
    """Request to update a session.

    Allows updating metadata, agent binding, and LLM configuration.
    """

    title: str | None = Field(None, max_length=200)
    agent_name: str | None = Field(None, description="Switch agent binding")
    metadata: dict[str, str] | None = Field(None, description="Replace session metadata")
    config: SessionConfig | None = Field(None, description="Update LLM configuration")


class SessionResumeRequest(BaseModel):
    """Request to resume an interrupted Deep Agents run."""

    decision: Literal["approve", "edit", "reject"] = Field(
        ..., description="Human decision for the interrupted tool call"
    )
    tool_call_id: str = Field(..., min_length=1, description="Interrupted tool call ID")
    tool_name: str = Field(..., min_length=1, description="Interrupted tool name")
    args: dict[str, Any] | None = Field(
        default=None, description="Optional replacement args when decision='edit'"
    )


# ============================================================================
# Message Models
# ============================================================================


class ToolCallResponse(BaseModel):
    """Tool call response model."""

    name: str = Field(..., description="Tool name")
    args: dict[str, Any] = Field(..., description="Tool arguments")
    id: str = Field(..., description="Tool call ID")


class MessageCreate(BaseModel):
    """Request to send a message."""

    content: str = Field(..., min_length=1, description="Message content")
    parent_id: str | None = Field(None, description="ID of parent message for threading")
    model: str | None = Field(
        None,
        description="Model to use for this message (e.g., 'gpt-4o', 'claude-3-sonnet'). Uses server default if not specified.",
    )
    callback_url: AnyHttpUrl | None = Field(
        default=None,
        description="If provided, Cognition POSTs the final completion payload to this URL when the run finishes.",
    )


class MessageResponse(BaseModel):
    """Message information response."""

    id: str = Field(..., description="Unique message identifier")
    session_id: str = Field(..., description="Associated session ID")
    role: Literal["user", "assistant", "system", "tool"] = Field(..., description="Message role")
    content: str | None = Field(None, description="Message content (if complete)")
    parent_id: str | None = Field(None, description="Parent message ID")
    model: str | None = Field(default=None, description="Model used for this message")
    created_at: datetime = Field(..., description="Message creation timestamp")
    tool_calls: list[ToolCallResponse] | None = Field(None, description="Tool invocations")
    tool_call_id: str | None = Field(None, description="ID of tool being responded to")
    token_count: int | None = Field(None, description="Token usage for this message")
    model_used: str | None = Field(None, description="Model that generated response")
    metadata: dict[str, Any] | None = Field(None, description="Additional metadata")


class MessageList(BaseModel):
    """List of messages response."""

    messages: list[MessageResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total number of messages")
    has_more: bool = Field(False, description="Whether more messages exist")


# ============================================================================
# SSE Event Models
# ============================================================================
# These Pydantic models serialize the canonical event types from agent/runtime.py
# for API responses. The canonical types are dataclasses used internally.


class TokenEvent(BaseModel):
    """Server-sent event: Token streaming.

    Serializes: server.app.agent.runtime.TokenEvent
    """

    event: Literal["token"] = "token"
    data: dict = Field(..., description="Token data with 'content' field")


class ToolCallEvent(BaseModel):
    """Server-sent event: Tool invocation.

    Serializes: server.app.agent.runtime.ToolCallEvent
    """

    event: Literal["tool_call"] = "tool_call"
    data: dict = Field(..., description="Tool call with 'name', 'args', 'id'")


class ToolResultEvent(BaseModel):
    """Server-sent event: Tool execution result.

    Serializes: server.app.agent.runtime.ToolResultEvent
    """

    event: Literal["tool_result"] = "tool_result"
    data: dict = Field(..., description="Tool result with 'tool_call_id', 'output', 'exit_code'")


class ErrorEvent(BaseModel):
    """Server-sent event: Error occurred.

    Serializes: server.app.agent.runtime.ErrorEvent
    """

    event: Literal["error"] = "error"
    data: dict = Field(..., description="Error with 'message' and optional 'code'")


class DoneEvent(BaseModel):
    """Server-sent event: Stream complete.

    Serializes: server.app.agent.runtime.DoneEvent
    """

    event: Literal["done"] = "done"
    data: dict = Field(default_factory=dict)


class DelegationEvent(BaseModel):
    """Server-sent event: Agent delegation to sub-agent.

    Serializes: server.app.agent.runtime.DelegationEvent
    """

    event: Literal["delegation"] = "delegation"
    data: dict = Field(..., description="Delegation info with 'from_agent', 'to_agent', 'task'")


class UsageEvent(BaseModel):
    """Server-sent event: Token usage update.

    Serializes: server.app.agent.runtime.UsageEvent
    """

    event: Literal["usage"] = "usage"
    data: dict = Field(
        ..., description="Usage with 'input_tokens', 'output_tokens', 'estimated_cost'"
    )


class PlanningEvent(BaseModel):
    """Server-sent event: agent planning update."""

    event: Literal["planning"] = "planning"
    data: dict = Field(..., description="Planning data with current todos")


class StepCompleteEvent(BaseModel):
    """Server-sent event: agent completed a todo step."""

    event: Literal["step_complete"] = "step_complete"
    data: dict = Field(..., description="Completed step details")


class StatusEvent(BaseModel):
    """Server-sent event: status update."""

    event: Literal["status"] = "status"
    data: dict = Field(..., description="Status update payload")


class InterruptEvent(BaseModel):
    """Server-sent event: agent waiting for human approval."""

    event: Literal["interrupt"] = "interrupt"
    data: dict = Field(
        ...,
        description="Interrupt info with tool call details and optional resume hints",
    )


class ReconnectedEvent(BaseModel):
    """Server-sent event: Stream reconnection confirmation (ISSUE-012).

    Emitted when a client reconnects with a Last-Event-ID header to resume
    a stream. The event confirms the reconnection and provides the last
    event ID that was received before the disconnect.

    Contract:
    - Server emits this as the first event after a successful reconnection
    - Clients should wait for this before processing new events
    - The 'last_event_id' matches the client's Last-Event-ID header value

    Payload:
    - last_event_id: The event ID the stream is resuming from
    - resumed: Always true to indicate successful resumption

    Example:
        event: reconnected
        data: {"last_event_id": "msg-123", "resumed": true}

    Serializes: EventBuilder.reconnected()
    """

    event: Literal["reconnected"] = "reconnected"
    data: dict = Field(..., description="Reconnection info with 'last_event_id' and 'resumed' flag")


# ============================================================================
# Health & Status Models
# ============================================================================


class CircuitBreakerStatus(BaseModel):
    """Circuit breaker status for a provider."""

    provider: str = Field(..., description="Provider name")
    state: str = Field(..., description="Circuit state (closed, open, half_open)")
    total_calls: int = Field(..., description="Total calls made")
    successful_calls: int = Field(..., description="Successful calls")
    failed_calls: int = Field(..., description="Failed calls")
    consecutive_failures: int = Field(..., description="Consecutive failures")
    last_failure_time: float | None = Field(None, description="Timestamp of last failure")


class HealthStatus(BaseModel):
    """Health check response."""

    status: Literal["healthy", "unhealthy"] = Field(..., description="Overall health status")
    version: str = Field(..., description="Server version")
    active_sessions: int = Field(..., description="Number of active sessions")
    circuit_breakers: list[CircuitBreakerStatus] = Field(
        default_factory=list, description="Circuit breaker status for each provider"
    )
    timestamp: datetime = Field(..., description="Health check timestamp")


class ReadyStatus(BaseModel):
    """Readiness probe response."""

    ready: bool = Field(..., description="Whether server is ready to accept requests")


# ============================================================================
# Feedback Models
# ============================================================================


class FeedbackCreate(BaseModel):
    """Request to submit feedback for a session."""

    feedback_type: str = Field(
        ..., description="Type of feedback: thumbs_up, thumbs_down, rating, correction, custom"
    )
    value: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Numeric value (e.g., 1.0 for thumbs up, 0.0 for thumbs down)",
    )
    trace_id: str | None = Field(None, description="Optional MLflow trace ID to attach feedback to")
    rationale: str | None = Field(None, max_length=1000, description="Explanation for the feedback")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")


class FeedbackResponse(BaseModel):
    """Feedback submission response."""

    id: str = Field(..., description="Unique feedback ID")
    session_id: str = Field(..., description="Associated session ID")
    feedback_type: str = Field(..., description="Type of feedback")
    value: float = Field(..., description="Feedback value")
    created_at: str = Field(..., description="Timestamp when feedback was created")


class EvaluationResponse(BaseModel):
    """Session evaluation response."""

    session_id: str = Field(..., description="Session ID")
    average_score: float = Field(..., description="Average score across all categories")
    scores: list[dict[str, Any]] = Field(default_factory=list, description="Individual scores")
    feedback_count: int = Field(0, description="Number of feedback entries")


# ============================================================================
# Error Models
# ============================================================================


class ErrorResponse(BaseModel):
    """Error response."""

    error: str = Field(..., description="Error message")
    code: str | None = Field(None, description="Error code")
    details: dict[str, Any] | None = Field(None, description="Additional error details")


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
    default_provider: str | None = Field(None, description="Default provider ID")
    default_model: str | None = Field(None, description="Default model ID")


# ============================================================================
# Config Update Models
# ============================================================================


class ConfigUpdateRequest(BaseModel):
    """Request to update infrastructure configuration via PATCH /config.

    Only rate_limit and observability settings are accepted here.
    Agent, LLM, and provider configuration has moved to the ConfigRegistry
    (use PATCH /agents/{name}, POST /models/providers, etc.).
    """

    rate_limit: dict[str, Any] | None = Field(
        None, description="Rate limiting settings (per_minute, burst)"
    )
    observability: dict[str, Any] | None = Field(
        None, description="Observability settings (otel_enabled, metrics_port, otel_endpoint)"
    )


class ConfigUpdateResponse(BaseModel):
    """Response from config update."""

    updated: bool = Field(..., description="Whether update was successful")
    changes: dict[str, Any] = Field(..., description="Fields that were changed")
    backup_created: bool = Field(..., description="Whether backup was created")
    timestamp: str = Field(..., description="Timestamp of update (ISO format)")


class ConfigRollbackResponse(BaseModel):
    """Response from config rollback."""

    rolled_back: bool = Field(..., description="Whether rollback was successful")
    timestamp: str = Field(..., description="Timestamp of rollback (ISO format)")


class GlobalProviderDefaultsResponse(BaseModel):
    """Global provider defaults exposed by the ConfigRegistry API."""

    provider: str
    model: str
    max_tokens: int | None = None
    system_prompt_type: Literal["file", "inline", "mlflow"]
    system_prompt_value: str


class GlobalProviderDefaultsUpdate(BaseModel):
    """Partial update model for global provider defaults."""

    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    system_prompt_type: Literal["file", "inline", "mlflow"] | None = None
    system_prompt_value: str | None = None


class GlobalAgentDefaultsResponse(BaseModel):
    """Global agent defaults exposed by the ConfigRegistry API."""

    memory: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    subagents: list[dict[str, Any]] = Field(default_factory=list)
    interrupt_on: dict[str, bool] = Field(default_factory=dict)
    response_format: str | None = None
    tool_token_limit_before_evict: int | None = None
    recursion_limit: int
    mcp_servers: dict[str, Any] = Field(default_factory=dict)


class GlobalAgentDefaultsUpdate(BaseModel):
    """Partial update model for global agent defaults."""

    memory: list[str] | None = None
    skills: list[str] | None = None
    subagents: list[dict[str, Any]] | None = None
    interrupt_on: dict[str, bool] | None = None
    response_format: str | None = None
    tool_token_limit_before_evict: int | None = None
    recursion_limit: int | None = None
    mcp_servers: dict[str, Any] | None = None


# ============================================================================
# Agent Models
# ============================================================================


class AgentResponse(BaseModel):
    """Agent information for API responses."""

    name: str = Field(..., description="Agent name")
    description: str | None = Field(None, description="Agent description")
    mode: Literal["primary", "subagent", "all"] = Field(..., description="Agent mode")
    hidden: bool = Field(..., description="Whether agent is hidden from listings")
    native: bool = Field(..., description="Whether agent is built-in")
    model: str | None = Field(
        None,
        description="Deprecated compatibility field. Use config.model instead.",
    )
    temperature: float | None = Field(
        None,
        description="Deprecated compatibility field. Use config.temperature instead.",
    )
    config: AgentConfigResponse | None = Field(
        None, description="Full runtime config for this agent"
    )
    response_format: str | None = Field(None, description="Structured output schema path")
    interrupt_on: dict[str, bool] = Field(
        default_factory=dict,
        description="Tool-name to HITL requirement map for this agent",
    )
    # ISSUE-009: Added tools and skills for better agent introspection
    tools: list[str] = Field(
        default_factory=list, description="Tool paths this agent has access to"
    )
    skills: list[str] = Field(
        default_factory=list, description="Skill directories this agent can use"
    )
    system_prompt: str | None = Field(None, description="Agent's system prompt")


class AgentConfigResponse(BaseModel):
    """Agent runtime configuration exposed over the API."""

    temperature: float | None = None
    max_tokens: int | None = None
    recursion_limit: int | None = None
    tool_token_limit_before_evict: int | None = None
    provider: str | None = None
    model: str | None = None
    timeout_seconds: float | None = None


class AgentList(BaseModel):
    """List of agents response."""

    agents: list[AgentResponse] = Field(
        default_factory=list, description="List of available agents"
    )


# ============================================================================
# Tool Models
# ============================================================================


class ToolResponse(BaseModel):
    """Tool information for API responses."""

    name: str = Field(..., description="Tool name")
    source_type: str = Field(
        ...,
        description=(
            "Origin of the tool: 'builtin' (built-in), 'file' (file-discovered), "
            "'api_code' (API-registered Python source), 'api_path' (API-registered module path)"
        ),
    )
    module: str | None = Field(None, description="Module path if loaded from a module path")
    description: str | None = Field(None, description="Tool description")
    enabled: bool = Field(True, description="Whether the tool is enabled")
    interrupt_on: bool = Field(
        default=False,
        description="Whether this tool is marked as requiring approval by default",
    )
    # Back-compat alias kept for existing consumers
    source: str = Field(..., description="Deprecated — use source_type")


class ToolList(BaseModel):
    """List of tools response."""

    tools: list[ToolResponse] = Field(default_factory=list, description="List of registered tools")
    count: int = Field(0, description="Total number of tools")


# ============================================================================
# Model Models (ISSUE-008)
# ============================================================================


class ModelInfo(BaseModel):
    """Information about an available LLM model.

    When backed by the models.dev catalog, all fields are populated.
    For models not in the catalog, only ``id`` and ``provider`` are guaranteed.
    """

    id: str = Field(..., description="Model identifier (value to pass in PATCH /sessions)")
    provider: str = Field(..., description="Provider name (e.g., 'openai', 'bedrock')")
    display_name: str | None = Field(None, description="Human-readable model name")
    context_window: int | None = Field(None, description="Context window size in tokens")
    output_limit: int | None = Field(None, description="Maximum output tokens")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Model capabilities (e.g., 'tool_call', 'reasoning', 'vision', 'structured_output')",
    )
    input_cost: float | None = Field(None, description="Input cost per million tokens (USD)")
    output_cost: float | None = Field(None, description="Output cost per million tokens (USD)")
    modalities: dict[str, list[str]] | None = Field(
        None,
        description="Supported modalities, e.g. {'input': ['text', 'image'], 'output': ['text']}",
    )
    family: str | None = Field(None, description="Model family (e.g., 'gpt', 'claude-sonnet')")
    status: str | None = Field(
        None, description="Model status: null (active), 'deprecated', or 'beta'"
    )


class ModelList(BaseModel):
    """List of available models."""

    models: list[ModelInfo] = Field(default_factory=list, description="List of available models")


# ============================================================================
# Skill Models
# ============================================================================


class SkillCreate(BaseModel):
    """Request to create or replace a skill."""

    name: str = Field(..., min_length=1, max_length=100, description="Skill identifier")
    path: str | None = Field(
        default=None,
        min_length=1,
        description="Filesystem path to skill directory or SKILL.md. Auto-generated if content is provided.",
    )
    enabled: bool = Field(default=True, description="Whether this skill is active")
    description: str | None = Field(default=None, description="Short description")
    content: str | None = Field(
        default=None,
        description="Full SKILL.md content (YAML frontmatter + markdown body). If provided, path is auto-generated.",
    )
    scope: dict[str, str] = Field(default_factory=dict, description="Scope (empty = global)")


class SkillUpdate(BaseModel):
    """Request to partially update a skill."""

    path: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    description: str | None = Field(default=None)
    content: str | None = Field(
        default=None, description="Full SKILL.md content (YAML frontmatter + markdown body)"
    )
    scope: dict[str, str] | None = Field(default=None)


class SkillResponse(BaseModel):
    """Skill information for API responses."""

    name: str
    path: str
    enabled: bool
    description: str | None = None
    content: str | None = Field(
        default=None, description="Full SKILL.md content (YAML frontmatter + markdown body)"
    )
    scope: dict[str, str] = Field(default_factory=dict)
    source: str = "api"


class SkillList(BaseModel):
    """List of skills response."""

    skills: list[SkillResponse] = Field(default_factory=list)
    count: int = 0


# ============================================================================
# Provider / Model CRUD Models
# ============================================================================


class ProviderCreate(BaseModel):
    """Request to create or replace a provider config."""

    id: str = Field(..., min_length=1, max_length=100, description="Unique provider identifier")
    provider: str = Field(..., min_length=1, description="Provider type (openai, bedrock, …)")
    model: str = Field(..., min_length=1, description="Model ID")
    display_name: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    priority: int = Field(default=0, description="Lower = tried first in fallback chain")
    max_retries: int = Field(default=2, ge=0)
    timeout: int | None = Field(
        default=None, gt=0, description="Provider request timeout in seconds"
    )
    api_key_env: str | None = Field(
        default=None,
        description="Name of the env var holding the API key (not the key itself)",
    )
    base_url: str | None = Field(default=None)
    region: str | None = Field(default=None)
    role_arn: str | None = Field(default=None)
    extra: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, str] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    """Request to partially update a provider config."""

    model: str | None = None
    display_name: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    max_retries: int | None = None
    timeout: int | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    region: str | None = None
    role_arn: str | None = None
    extra: dict[str, Any] | None = None


class ProviderResponse(BaseModel):
    """Provider config for API responses."""

    id: str
    provider: str
    model: str
    display_name: str | None = None
    enabled: bool
    priority: int
    max_retries: int
    timeout: int | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    region: str | None = None
    role_arn: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, str] = Field(default_factory=dict)
    source: str = "api"


class ProviderConfigList(BaseModel):
    """List of providers response."""

    providers: list[ProviderResponse] = Field(default_factory=list)
    count: int = 0


class ProviderTestResponse(BaseModel):
    """Result of a provider connectivity test."""

    success: bool
    provider: str
    model: str
    message: str
    response_preview: str | None = None


# ============================================================================
# Agent CRUD Models
# ============================================================================


class AgentCreate(BaseModel):
    """Request to create or replace an agent definition."""

    name: str = Field(..., min_length=1, max_length=100, description="Agent identifier")
    system_prompt: str = Field(default="", description="System prompt text")
    description: str | None = Field(default=None)
    mode: Literal["primary", "subagent", "all"] = Field(default="primary")
    hidden: bool = Field(default=False)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    memory: list[str] = Field(default_factory=list)
    interrupt_on: dict[str, bool] = Field(default_factory=dict)
    response_format: str | None = Field(
        default=None, description="Dotted path to structured output schema"
    )
    model: str | None = Field(default=None, description="Default model override")
    temperature: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    recursion_limit: int | None = Field(default=None)
    tool_token_limit_before_evict: int | None = Field(default=None)
    provider: str | None = Field(default=None)
    timeout_seconds: float | None = Field(default=None)
    middleware: list[Any] = Field(default_factory=list)
    scope: dict[str, str] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """Request to partially update an agent definition."""

    system_prompt: str | None = None
    description: str | None = None
    mode: Literal["primary", "subagent", "all"] | None = None
    hidden: bool | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    memory: list[str] | None = None
    interrupt_on: dict[str, bool] | None = None
    response_format: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    recursion_limit: int | None = None
    tool_token_limit_before_evict: int | None = None
    provider: str | None = None
    timeout_seconds: float | None = None
    middleware: list[Any] | None = None


# ============================================================================
# Tool CRUD Models
# ============================================================================


class ToolCreate(BaseModel):
    """Request to register a tool in the ConfigRegistry.

    Exactly one of ``path`` or ``code`` must be provided:

    - ``path``: Python module path (e.g. ``mypackage.tools.jira``) or file
      path. The module must be importable by the Cognition server process.
    - ``code``: Full Python source code. Stored in the DB and executed at
      runtime via ``exec()``. Suitable for builder applications that cannot
      access the server filesystem.

    Security note: Tool code executes with full Python privileges. This
    endpoint should be restricted to authorized administrators.
    """

    name: str = Field(..., min_length=1, max_length=100, description="Tool identifier")
    path: str | None = Field(default=None, description="Module or file path for the tool")
    code: str | None = Field(default=None, description="Python source code to execute at runtime")
    enabled: bool = Field(default=True)
    description: str | None = Field(default=None)
    interrupt_on: bool = Field(default=False)
    scope: dict[str, str] = Field(default_factory=dict)


class ToolUpdate(BaseModel):
    """Request to partially update a tool registration."""

    path: str | None = Field(default=None)
    code: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    description: str | None = Field(default=None)
    interrupt_on: bool | None = Field(default=None)
