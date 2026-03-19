"""Pydantic models for the ConfigRegistry system.

These models represent the runtime configuration entities that move out of
Settings into the DB-backed ConfigRegistry: providers, agents (seeds from
AgentDefinition), tools, skills, and config-change events.

Design notes:
- Scope is a plain dict[str, str] (e.g. {"user": "alice", "project": "myapp"}).
  An empty dict means "global / no scope" — applies to everyone.
- `source` distinguishes file-bootstrapped rows ("file") from API-written rows
  ("api"). File rows are re-seeded on startup only when absent from DB; API
  rows always win and are never overwritten by file bootstrap.
- Credentials (API keys, AWS secrets) are *never* stored here. `ProviderConfig`
  holds only the env-var *name* that carries the key at runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Provider / LLM
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider entry in the registry.

    Credentials are referenced by env-var name, never stored inline.

    Attributes:
        id: Unique provider identifier (e.g. "openai-gpt4o", "bedrock-claude").
        provider: Provider type key ("openai", "bedrock", "openai_compatible", "mock").
        model: Model ID for this entry (e.g. "gpt-4o").
        display_name: Human-readable label for UIs.
        enabled: Whether the provider is active.
        priority: Lower values are tried first in fallback chains.
        max_retries: Retry attempts before giving up.
        api_key_env: Name of the env var holding the API key (not the key itself).
        base_url: Optional base URL override for OpenAI-compatible endpoints.
        region: AWS region for Bedrock providers.
        role_arn: IAM role ARN for cross-account Bedrock access.
        extra: Provider-specific options that don't have first-class fields.
        scope: Scope this entry applies to. Empty dict = global default.
        source: "file" for bootstrap rows, "api" for API-written rows.
    """

    id: str = Field(..., min_length=1, max_length=100)
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    display_name: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    priority: int = Field(default=0)
    max_retries: int = Field(default=2, ge=0)

    # Credential references — env var names only, not values
    api_key_env: str | None = Field(
        default=None,
        description="Name of the env var holding the API key (e.g. 'OPENAI_API_KEY').",
    )
    base_url: str | None = Field(default=None)
    region: str | None = Field(default=None)
    role_arn: str | None = Field(default=None)
    extra: dict[str, Any] = Field(default_factory=dict)

    # Registry metadata
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="file")

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Ensure provider ID is slug-like."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Provider id must be alphanumeric with hyphens/underscores only: {v}")
        return v


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class ToolRegistration(BaseModel):
    """A tool registered in the config registry.

    Tools can be file-path based (.cognition/tools/my_tool.py) or
    module-path based (server.app.tools.my_tool).

    Attributes:
        name: Tool identifier (must match the BaseTool.name).
        path: File path or module path to load the tool from.
        enabled: Whether this tool is active.
        description: Optional description for documentation purposes.
        scope: Scope this entry applies to. Empty dict = global.
        source: "file" or "api".
    """

    name: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1)
    enabled: bool = Field(default=True)
    description: str | None = Field(default=None)
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="file")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate tool name format."""
        if not v.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError(
                f"Tool name must be alphanumeric with hyphens/underscores/dots only: {v}"
            )
        return v


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


class SkillDefinition(BaseModel):
    """A skill registered in the config registry.

    Skills are Markdown files that inject domain-specific instructions into
    an agent's context window via progressive disclosure.

    Attributes:
        name: Skill identifier (e.g. "typescript-best-practices").
        path: Filesystem path to the skill directory or SKILL.md file.
        enabled: Whether this skill is active.
        description: Short description shown in skill listings.
        content: Full SKILL.md content (YAML frontmatter + markdown body).
        scope: Scope this entry applies to. Empty dict = global.
        source: "file" or "api".
    """

    name: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1)
    enabled: bool = Field(default=True)
    description: str | None = Field(default=None)
    content: str | None = Field(default=None)
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="file")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate skill name format."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Skill name must be alphanumeric with hyphens/underscores only: {v}")
        return v


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------


class McpServerRegistration(BaseModel):
    """An MCP (Model Context Protocol) server registered in the config registry.

    Attributes:
        name: Server identifier.
        url: HTTP/HTTPS URL for the MCP server.
        headers: Optional request headers (e.g. auth tokens).
        enabled: Whether this server is active.
        scope: Scope this entry applies to.
        source: "file" or "api".
    """

    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = Field(default=True)
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="file")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URL uses HTTP/HTTPS only."""
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"MCP server URL must start with http:// or https://, got: {v!r}")
        return v


# ---------------------------------------------------------------------------
# Config change / invalidation events
# ---------------------------------------------------------------------------

EntityType = Literal["provider", "tool", "skill", "agent", "mcp_server"]
OperationType = Literal["upsert", "delete"]


class ConfigChange(BaseModel):
    """A single config change record stored in the config_changes table.

    Used by the dispatcher to broadcast invalidation events.

    Attributes:
        id: Row identifier from DB.
        entity_type: What kind of entity changed.
        name: Entity name that changed.
        scope: Scope of the changed entity.
        operation: "upsert" (create or update) or "delete".
        changed_at: When the change was recorded.
    """

    id: int
    entity_type: EntityType
    name: str
    scope: dict[str, str]
    operation: OperationType
    changed_at: datetime


class ConfigChangeEvent(BaseModel):
    """In-memory change event emitted by the dispatcher to subscribers.

    Subscribers (e.g. AgentDefinitionRegistry, agent cache) receive this
    and decide whether/how to invalidate their local state.

    Attributes:
        entity_type: What kind of entity changed.
        name: Entity name that changed.
        scope: Scope of the changed entity.
        operation: "upsert" or "delete".
    """

    entity_type: EntityType
    name: str
    scope: dict[str, str]
    operation: OperationType


# ---------------------------------------------------------------------------
# Global provider defaults (moved from Settings)
# ---------------------------------------------------------------------------


class GlobalProviderDefaults(BaseModel):
    """The "global" provider defaults — replaces Settings.llm_* fields.

    Stored as a special entity_type="provider", name="__global__" row.
    These are the fallback when no scope-specific override is present.

    Attributes:
        provider: Primary provider type ("openai", "bedrock", …).
        model: Primary model ID.
        max_tokens: Default max tokens.
        system_prompt_type: "file" | "inline" | "mlflow".
        system_prompt_value: Prompt text, filename, or mlflow ref.
    """

    provider: str = Field(default="openai_compatible")
    model: str = Field(default="gpt-4o")
    max_tokens: int | None = Field(default=20000)
    system_prompt_type: Literal["file", "inline", "mlflow"] = Field(default="file")
    system_prompt_value: str = Field(default="system")


# ---------------------------------------------------------------------------
# Agent defaults (moved from Settings.agent_*)
# ---------------------------------------------------------------------------


class GlobalAgentDefaults(BaseModel):
    """The "global" agent defaults — replaces Settings.agent_* fields.

    Stored as entity_type="agent", name="__defaults__" row.

    Attributes:
        memory: List of memory file paths.
        skills: List of skill directory paths.
        subagents: Subagent specs (list of dicts).
        interrupt_on: Tool-name -> bool map for human-in-the-loop.
        recursion_limit: Max ReAct recursion depth.
        mcp_servers: MCP server config dicts keyed by name.
    """

    memory: list[str] = Field(default_factory=lambda: ["AGENTS.md"])
    skills: list[str] = Field(default_factory=lambda: [".cognition/skills/"])
    subagents: list[dict[str, Any]] = Field(default_factory=list)
    interrupt_on: dict[str, bool] = Field(default_factory=dict)
    recursion_limit: int = Field(default=1000, gt=0)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ConfigChange",
    "ConfigChangeEvent",
    "EntityType",
    "GlobalAgentDefaults",
    "GlobalProviderDefaults",
    "McpServerRegistration",
    "OperationType",
    "ProviderConfig",
    "SkillDefinition",
    "ToolRegistration",
]
