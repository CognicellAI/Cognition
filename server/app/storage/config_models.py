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

from pydantic import BaseModel, Field, field_validator, model_validator

from server.app.agent.definition import AsyncSubagentConfig, ContextPolicy

PROVIDER_TYPES = {
    "openai",
    "anthropic",
    "bedrock",
    "mock",
    "openai_compatible",
    "google_genai",
    "google_vertexai",
}

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
    timeout: int | None = Field(default=None, gt=0)

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

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Ensure provider type is one of Cognition's supported runtime providers."""
        if v not in PROVIDER_TYPES:
            supported = ", ".join(sorted(PROVIDER_TYPES))
            raise ValueError(f"Unsupported provider '{v}'. Supported providers: {supported}")
        return v

    @model_validator(mode="after")
    def validate_provider_specific_fields(self) -> ProviderConfig:
        """Reject provider-specific fields that don't apply to the selected provider."""
        if self.provider == "openai_compatible":
            if not self.base_url:
                raise ValueError("openai_compatible providers require base_url")
        elif self.base_url is not None:
            raise ValueError("base_url is only valid for openai_compatible providers")

        if self.provider == "bedrock":
            if not self.region:
                raise ValueError("bedrock providers require region")
        elif self.region is not None:
            raise ValueError("region is only valid for bedrock providers")

        if self.role_arn is not None and self.provider != "bedrock":
            raise ValueError("role_arn is only valid for bedrock providers")

        return self


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class ToolRegistration(BaseModel):
    """A tool registered in the config registry.

    Tools can be registered in two ways:
    - ``path``: A Python module path (e.g. ``mypackage.tools.jira``) or file
      path (e.g. ``.cognition/tools/my_tool.py``). The module must be importable
      by the server process — suitable for pre-installed packages.
    - ``code``: Full Python source code stored directly in the DB. Cognition
      executes it at runtime via ``exec()``. Suitable for builder applications
      that cannot access the server filesystem (e.g. separate containers).

    Exactly one of ``path`` or ``code`` must be provided.

    Security note: Tool code executes with full Python privileges inside the
    sandbox backend. ``POST /tools`` should be restricted to authorized
    administrators at the Gateway/proxy layer.

    Attributes:
        name: Tool identifier.
        path: File path or module path to load the tool from.
        code: Python source code to execute at runtime.
        enabled: Whether this tool is active.
        description: Optional description for documentation purposes.
        scope: Scope this entry applies to. Empty dict = global.
        source: "file" (bootstrapped from disk) or "api" (registered via API).
    """

    name: str = Field(..., min_length=1, max_length=100)
    path: str | None = Field(default=None)
    code: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    description: str | None = Field(default=None)
    interrupt_on: bool = Field(
        default=False,
        description="Whether this tool should require human approval when used by agents that inherit tool-level interrupt defaults.",
    )
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

    @model_validator(mode="after")
    def validate_path_or_code(self) -> ToolRegistration:
        """Validate that exactly one of path or code is provided."""
        has_path = self.path is not None and self.path.strip() != ""
        has_code = self.code is not None and self.code.strip() != ""
        if not has_path and not has_code:
            raise ValueError("Either 'path' or 'code' must be provided.")
        if has_path and has_code:
            raise ValueError("Provide either 'path' or 'code', not both.")
        return self


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
        transport: Transport protocol ("sse" or "streamable_http").
    """

    name: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = Field(default=True)
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="file")
    transport: Literal["sse", "streamable_http"] = Field(default="sse")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URL uses HTTP/HTTPS only."""
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"MCP server URL must start with http:// or https://, got: {v!r}")
        return v


# ---------------------------------------------------------------------------
# Sandbox Profile
# ---------------------------------------------------------------------------


class LambdaMicroVmIdlePolicy(BaseModel):
    """Idle lifecycle policy for an AWS Lambda MicroVM sandbox profile."""

    max_idle_duration_seconds: int = Field(default=900, gt=0)
    suspended_duration_seconds: int | None = Field(default=1800, gt=0)
    auto_resume_enabled: bool = Field(default=True)


class SandboxProfile(BaseModel):
    """Builder-managed sandbox profile for the AWS Lambda MicroVM backend.

    V1 consumes prebuilt AWS Lambda MicroVM image ARNs. Cognition stores only
    trusted profile metadata and never creates, updates, or persists runtime
    credentials for the image or sandbox.
    """

    name: str = Field(..., min_length=1, max_length=100)
    backend: Literal["aws_lambda_microvm"] = Field(default="aws_lambda_microvm")
    image_arn: str = Field(..., min_length=1)
    image_version: str | None = Field(default=None)
    region: str | None = Field(default=None)
    ingress_network_connector_arns: list[str] = Field(default_factory=list)
    egress_mode: Literal["internet", "vpc"] = Field(default="internet")
    egress_network_connector_arns: list[str] = Field(default_factory=list)
    idle_policy: LambdaMicroVmIdlePolicy | None = Field(default=None)
    maximum_duration_seconds: int = Field(default=3600, gt=0, le=28800)
    port: int = Field(default=8080, ge=1, le=65535)
    token_expiration_minutes: int = Field(default=30, gt=0)
    default_execution_role_arn: str | None = Field(default=None)
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="file")
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate sandbox profile name format."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"Sandbox profile name must be alphanumeric with hyphens/underscores only: {v}"
            )
        return v

    @field_validator("image_arn")
    @classmethod
    def validate_image_arn(cls, v: str) -> str:
        """Ensure the profile references a Lambda MicroVM image ARN."""
        if not v.startswith("arn:aws:lambda:") or ":microvm-image:" not in v:
            raise ValueError("image_arn must be an AWS Lambda MicroVM image ARN")
        return v

    @field_validator(
        "ingress_network_connector_arns",
        "egress_network_connector_arns",
    )
    @classmethod
    def validate_network_connector_arns(cls, v: list[str]) -> list[str]:
        """Ensure connector fields only contain ARN-shaped values."""
        for arn in v:
            if not arn.startswith("arn:aws:"):
                raise ValueError(f"Network connector must be an AWS ARN: {arn!r}")
        return v

    @model_validator(mode="after")
    def validate_egress_config(self) -> SandboxProfile:
        """Require explicit egress connectors for VPC-only egress."""
        if self.egress_mode == "vpc" and not self.egress_network_connector_arns:
            raise ValueError(
                "egress_network_connector_arns is required when egress_mode is 'vpc'"
            )
        return self


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

ARTIFACT_TYPES = Literal["scratch", "artifact", "contract", "eval", "memory", "policy"]


class ArtifactDefinition(BaseModel):
    """A persistent artifact stored in the config registry.

    Artifacts are durable, scope-aware files that agents and builders can
    read, write, list, and diff. They provide explicit state outside the
    model context window, used for long-running agent handoffs.

    Attributes:
        id: Unique artifact identifier (UUID).
        name: Human-readable name (e.g. "feature-list", "progress").
        artifact_type: Route category (scratch, artifact, contract, eval,
            memory, policy).
        path: Virtual path within the type route (e.g. "feature_list.json").
        content: File content (JSON, Markdown, or plain text).
        content_type: MIME or type tag (e.g. "application/json").
        version: Monotonic version number (incremented on update).
        parent_version: Version this artifact descends from (None for v1).
        run_id: Optional associated run identifier.
        checkpoint_id: Optional associated checkpoint identifier.
        visibility: "private" (scope-only), "run" (visible within session),
            "public" (visible to all scope members).
        scope: Scope this artifact belongs to.
        source: "file" or "api".
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    artifact_type: ARTIFACT_TYPES = Field(default="scratch")
    path: str = Field(default="")
    content: str = Field(default="")
    content_type: str = Field(default="text/plain")
    version: int = Field(default=1, ge=1)
    parent_version: int | None = Field(default=None, ge=1)
    run_id: str | None = Field(default=None)
    checkpoint_id: str | None = Field(default=None)
    visibility: Literal["private", "run", "public"] = Field(default="private")
    scope: dict[str, str] = Field(default_factory=dict)
    source: Literal["file", "api"] = Field(default="api")
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate artifact name format."""
        if not v.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError(
                f"Artifact name must be alphanumeric with hyphens/underscores/dots only: {v}"
            )
        return v

    @field_validator("artifact_type")
    @classmethod
    def validate_artifact_type(cls, v: str) -> str:
        """Ensure artifact type is one of the known route categories."""
        allowed = {"scratch", "artifact", "contract", "eval", "memory", "policy"}
        if v not in allowed:
            raise ValueError(f"Artifact type must be one of {sorted(allowed)}, got: {v!r}")
        return v


# ---------------------------------------------------------------------------
# Config change / invalidation events
# ---------------------------------------------------------------------------

EntityType = Literal[
    "provider",
    "tool",
    "skill",
    "agent",
    "mcp_server",
    "sandbox_profile",
]
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

    Subscribers (e.g. ConfigStore agent cache) receive this
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
        interrupt_on: Tool-name -> bool or rich human-in-the-loop config.
        permissions: Deep Agents filesystem permission rules.
        recursion_limit: Max ReAct recursion depth.
        mcp_servers: MCP server config dicts keyed by name.
    """

    memory: list[str] = Field(default_factory=lambda: ["AGENTS.md"])
    skills: list[str] = Field(default_factory=list)
    subagents: list[dict[str, Any]] = Field(default_factory=list)
    async_subagents: list[AsyncSubagentConfig] = Field(default_factory=list)
    interrupt_on: dict[str, Any] = Field(default_factory=dict)
    permissions: list[dict[str, Any]] = Field(default_factory=list)
    response_format: str | None = Field(default=None)
    tool_token_limit_before_evict: int | None = Field(default=None, gt=0)
    context_policy: ContextPolicy | None = Field(default=None)
    recursion_limit: int = Field(default=1000, gt=0)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ARTIFACT_TYPES",
    "ArtifactDefinition",
    "ConfigChange",
    "ConfigChangeEvent",
    "EntityType",
    "GlobalAgentDefaults",
    "GlobalProviderDefaults",
    "McpServerRegistration",
    "OperationType",
    "ProviderConfig",
    "LambdaMicroVmIdlePolicy",
    "SandboxProfile",
    "SkillDefinition",
    "ToolRegistration",
]
