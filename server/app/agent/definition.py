"""Declarative agent configuration for Cognition.

This module defines Pydantic models for declarative agent configuration,
enabling agent definitions via YAML files. This supports the P1-5 roadmap item
for Declarative AgentDefinition.

P3 Multi-Agent Registry:
- Extended with mode, description, hidden, native fields
- Support for Markdown frontmatter format (.cognition/agents/*.md)
- Translation to Deep Agents SubAgent TypedDict via to_subagent()
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger(__name__)

from langchain_core.tools import BaseTool
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _validate_a2a_public_interface_url(value: str) -> str:
    """Validate an absolute public HTTP(S) A2A interface URL without rewriting it."""
    if not value or any(character.isspace() for character in value):
        raise ValueError("A2A public interface URL must not be empty or contain whitespace")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("A2A public interface URL is malformed") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("A2A public interface URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("A2A public interface URL must not contain credentials")
    if parsed.fragment:
        raise ValueError("A2A public interface URL must not contain a fragment")
    return value


A2APublicInterfaceUrl = Annotated[
    str,
    AfterValidator(_validate_a2a_public_interface_url),
]


def _validate_media_types(values: list[str]) -> list[str]:
    """Validate and de-duplicate Agent Card MIME media types."""
    normalized: list[str] = []
    for value in values:
        if (
            not value
            or value != value.strip()
            or any(character.isspace() for character in value)
            or value.count("/") != 1
        ):
            raise ValueError(f"Invalid A2A media type: {value!r}")
        media_type, subtype = value.split("/", 1)
        if not media_type or not subtype:
            raise ValueError(f"Invalid A2A media type: {value!r}")
        if value not in normalized:
            normalized.append(value)
    return normalized


class A2APublicSkill(BaseModel):
    """Builder-published capability descriptor for an A2A Agent Card."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=2000)
    tags: list[str] = Field(..., min_length=1)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=list)
    output_modes: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        """Require non-empty, unique discovery tags."""
        if any(not value.strip() for value in values):
            raise ValueError("A2A skill tags must not be empty")
        return list(dict.fromkeys(values))

    @field_validator("input_modes", "output_modes")
    @classmethod
    def validate_modes(cls, values: list[str]) -> list[str]:
        """Validate optional per-skill MIME mode overrides."""
        return _validate_media_types(values)


class A2AConfig(BaseModel):
    """Builder-controlled A2A exposure and public Agent Card presentation."""

    model_config = ConfigDict(extra="forbid")

    exposed: bool = Field(default=False)
    public_interface_url: A2APublicInterfaceUrl | None = Field(default=None)
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain", "application/json"],
        min_length=1,
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain", "application/json"],
        min_length=1,
    )
    skills: list[A2APublicSkill] = Field(default_factory=list)

    @field_validator("default_input_modes", "default_output_modes")
    @classmethod
    def validate_default_modes(cls, values: list[str]) -> list[str]:
        """Validate required card-level MIME modes."""
        return _validate_media_types(values)

    @field_validator("skills")
    @classmethod
    def validate_unique_skill_ids(cls, values: list[A2APublicSkill]) -> list[A2APublicSkill]:
        """Require stable, unique public skill identifiers."""
        ids = [skill.id for skill in values]
        if len(ids) != len(set(ids)):
            raise ValueError("A2A public skill IDs must be unique")
        return values


class ContextPolicy(BaseModel):
    """Declarative context management policy for an agent.

    Cognition uses this as the builder-visible policy surface and maps the
    supported pieces onto Deep Agents primitives. It is intentionally metadata
    and policy only; it does not expose raw prompt contents.
    """

    max_input_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Advisory input-token budget surfaced in context events/debug APIs.",
    )
    tool_token_limit_before_evict: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Advisory per-tool token budget. Deep Agents 0.6.2 no longer accepts "
            "this as a create_deep_agent kwarg; Cognition surfaces it for policy "
            "visibility and future enforcement."
        ),
    )
    summarization_enabled: bool = Field(
        default=True,
        description="Whether Cognition should attach Deep Agents summarization middleware.",
    )
    summarizer_model: str | None = Field(
        default=None,
        description="Reserved model/profile hint for future summarizer selection.",
    )
    offload_large_tool_outputs: bool = Field(
        default=True,
        description="Reserved policy hint for future artifact offload enforcement.",
    )
    retention: dict[str, str] = Field(
        default_factory=dict,
        description="Reserved per-source retention hints for future enforcement.",
    )


class AgentConfig(BaseModel):
    """Agent runtime configuration.

    Attributes:
        temperature: Sampling temperature for LLM (0.0-2.0).
        max_tokens: Maximum tokens to generate.
        provider: LLM provider to use (mock, openai, bedrock, etc.).
        model: Model name to use.
        timeout_seconds: Request timeout in seconds.
        sandbox_profile: Trusted sandbox profile selected for this agent.
        sandbox_execution_role_arn: Trusted IAM role ARN assigned to this
            agent's sandbox runtime.
    """

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    recursion_limit: int | None = Field(default=None, gt=0)
    tool_token_limit_before_evict: int | None = Field(default=None, gt=0)
    context_policy: ContextPolicy | None = Field(default=None)
    excluded_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)
    timeout_seconds: float | None = Field(default=None, gt=0)
    sandbox_profile: str | None = Field(default=None)
    sandbox_execution_role_arn: str | None = Field(default=None)


MCPAuthType = Literal[
    "none",
    "mcp_oauth",
    "workload_token_exchange",
    "static_bearer",
]


class McpNoAuthConfig(BaseModel):
    """Anonymous MCP transport with no Cognition-provided authentication."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["none"] = "none"


class McpOAuthConfig(BaseModel):
    """Standard MCP OAuth authorization handled by the upstream MCP SDK."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mcp_oauth"] = "mcp_oauth"


class McpWorkloadTokenExchangeAuthConfig(BaseModel):
    """Deployment-profile selection for workload OAuth token exchange."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["workload_token_exchange"] = "workload_token_exchange"
    profile: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class McpStaticBearerAuthConfig(BaseModel):
    """Environment-backed static bearer transport authentication."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["static_bearer"] = "static_bearer"
    env: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


McpAuthConfig = Annotated[
    McpNoAuthConfig
    | McpOAuthConfig
    | McpWorkloadTokenExchangeAuthConfig
    | McpStaticBearerAuthConfig,
    Field(discriminator="type"),
]


class AgentMcpServerConfig(BaseModel):
    """Agent-scoped MCP server definition."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1)
    transport: Literal["streamable_http"] = Field(default="streamable_http")
    required: bool = Field(default=True)
    auth: McpAuthConfig = Field(default_factory=McpNoAuthConfig)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("Agent MCP server URL must not be empty or contain whitespace")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Agent MCP server URL is malformed") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
            raise ValueError(
                "Agent MCP servers must use absolute HTTP/HTTPS URLs; local stdio "
                "servers are not supported"
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Agent MCP server URLs must not contain credentials")
        if parsed.fragment:
            raise ValueError("Agent MCP server URLs must not contain fragments")
        return value


class AgentMcpConfig(BaseModel):
    """Agent-owned MCP configuration."""

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, AgentMcpServerConfig] = Field(default_factory=dict)

    @field_validator("servers")
    @classmethod
    def validate_server_aliases(
        cls, value: dict[str, AgentMcpServerConfig]
    ) -> dict[str, AgentMcpServerConfig]:
        for alias in value:
            if not alias.replace("-", "").replace("_", "").isalnum():
                raise ValueError(
                    "MCP server aliases must be alphanumeric with hyphens/underscores only"
                )
        return value


class FilesystemPermissionConfig(BaseModel):
    """Deep Agents filesystem permission rule.

    This is an agent/app-level capability policy for built-in filesystem
    operations. It is not a tenant authorization mechanism.
    """

    operations: list[Literal["read", "write"]] = Field(..., min_length=1)
    paths: list[str] = Field(..., min_length=1)
    mode: Literal["allow", "deny"] = Field(default="allow")


class HumanInTheLoopConfig(BaseModel):
    """Deep Agents human-in-the-loop policy for a tool."""

    allowed_decisions: list[Literal["approve", "edit", "reject", "respond"]] = Field(
        ..., min_length=1
    )
    description: str | None = None
    args_schema: dict[str, Any] | None = None


class AsyncSubagentConfig(BaseModel):
    """Experimental remote Agent Protocol async subagent.

    This v0.10 surface intentionally excludes arbitrary request headers to avoid
    introducing a new secret-reference or secret-injection path. Builders can use
    trusted networks or gateway-level authentication until a scoped credential
    mechanism exists. This is distinct from simple in-process supervisor/subagent
    patterns; it requires an Agent Protocol-compatible worker deployment.
    """

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1)
    graph_id: str = Field(..., min_length=1)
    url: str | None = Field(default=None, min_length=1)


class SubagentDefinition(BaseModel):
    """Definition of a subagent.

    Subagents are specialized agents that handle specific tasks
    within the context of a parent agent.

    Attributes:
        name: Unique name for the subagent.
        description: Human-readable description of the subagent's purpose.
        system_prompt: System prompt for the subagent.
        tools: Tool module paths available to this subagent.
        config: Runtime configuration overrides.
    """

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None)
    system_prompt: str = Field(..., min_length=1)
    tools: list[str] = Field(default_factory=list)
    config: AgentConfig | None = Field(default=None)
    permissions: list[FilesystemPermissionConfig] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate subagent name format."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"Subagent name must be alphanumeric with hyphens/underscores only: {v}"
            )
        return v


class AgentSkillBundle(BaseModel):
    """Complete Deep Agents skill bundle owned by one Agent definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., min_length=1, max_length=100)
    content: str = Field(
        ...,
        min_length=1,
        description="Complete SKILL.md content, including YAML frontmatter.",
    )
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Supporting text files keyed by safe relative POSIX paths.",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Skill name must be alphanumeric with hyphens/underscores only")
        return value

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:
            parts = path.split("/")
            if (
                not path
                or path == "SKILL.md"
                or path.startswith("/")
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise ValueError("Skill bundle file paths must be safe relative POSIX paths")
        return value


class AgentDefinition(BaseModel):
    """Declarative agent definition.

    This model defines a complete agent configuration including tools,
    skills, memory, subagents, and runtime configuration. It enables
    agents to be defined entirely via YAML configuration files.

    Attributes:
        name: Unique agent identifier.
        display_name: Optional human-readable name for public presentation.
        a2a: A2A exposure and public Agent Card presentation configuration.
        system_prompt: System prompt that defines agent behavior.
        tools: List of attached tool names.
        skills: Complete skill bundles owned by this Agent revision.
        memory: List of memory file paths.
        subagents: Nested subagent definitions.
        interrupt_on: Tool-name to HITL policy map.
        permissions: Deep Agents filesystem permission rules.
        middleware: Middleware class paths.
        config: Runtime configuration (temperature, max_tokens, etc.).
    """

    name: str = Field(..., min_length=1, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    system_prompt: str = Field(..., min_length=1)
    tools: list[str] = Field(default_factory=list)
    skills: list[AgentSkillBundle] = Field(default_factory=list)
    memory: list[str] = Field(default_factory=list)
    subagents: list[SubagentDefinition] = Field(default_factory=list)
    async_subagents: list[AsyncSubagentConfig] = Field(default_factory=list)
    interrupt_on: dict[str, HumanInTheLoopConfig] = Field(default_factory=dict)
    permissions: list[FilesystemPermissionConfig] = Field(default_factory=list)
    response_format: str | None = Field(default=None)
    middleware: list[str | dict[str, Any]] = Field(default_factory=list)
    mcp: AgentMcpConfig = Field(default_factory=AgentMcpConfig)

    config: AgentConfig = Field(default_factory=AgentConfig)
    # P3 Multi-Agent Registry additions
    mode: Literal["primary", "subagent", "all"] = Field(default="all")
    description: str | None = Field(default=None)
    hidden: bool = Field(default=False)
    native: bool = Field(
        default=False,
        description=(
            "Legacy compatibility flag; Cognition does not create native Agents"
        ),
    )
    a2a: A2AConfig = Field(default_factory=A2AConfig)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate agent name format."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Agent name must be alphanumeric with hyphens/underscores only: {v}")
        return v

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v: list[str]) -> list[str]:
        """Validate attached tool names."""
        for tool_name in v:
            if not tool_name:
                raise ValueError("Tool path cannot be empty")
            if "/" in tool_name or tool_name.endswith(".py") or "." in tool_name:
                raise ValueError(
                    "Agent tools must be registry tool names, not module or file paths"
                )
        return v

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, value: list[AgentSkillBundle]) -> list[AgentSkillBundle]:
        names = [skill.name for skill in value]
        if len(names) != len(set(names)):
            raise ValueError("Agent skill bundle names must be unique")
        return value

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, v: list[str]) -> list[str]:
        """Validate memory file paths."""
        for memory_path in v:
            if not memory_path:
                raise ValueError("Memory path cannot be empty")
        return v

    @field_validator("middleware")
    @classmethod
    def validate_middleware(cls, v: list[str | dict[str, Any]]) -> list[str | dict[str, Any]]:
        """Validate middleware class paths or dict specs."""
        for item in v:
            if isinstance(item, str):
                if not item:
                    raise ValueError("Middleware path cannot be empty")
                parts = item.split(".")
                if len(parts) < 2:
                    raise ValueError(f"Middleware path must be a valid Python class path: {item}")
            elif isinstance(item, dict):
                if "name" not in item:
                    raise ValueError("Middleware dict must have a 'name' field")
            else:
                raise ValueError(
                    f"Middleware must be a string path or dict spec, got: {type(item)}"
                )
        return v

    def to_yaml(self) -> str:
        """Export agent definition to YAML string.

        Returns:
            YAML representation of the agent definition.

        Raises:
            ImportError: If PyYAML is not installed.
        """
        if not HAS_YAML:
            raise ImportError(
                "PyYAML is required for YAML export. Install with: uv pip install pyyaml"
            )

        # Convert to dict for serialization
        data = self.model_dump()
        result: str = yaml.dump(data, default_flow_style=False, sort_keys=False)
        return result

    def save_to_file(self, path: str | Path) -> None:
        """Save agent definition to YAML file.

        Args:
            path: Path to save the YAML file.

        Raises:
            ImportError: If PyYAML is not installed.
        """
        if not HAS_YAML:
            raise ImportError(
                "PyYAML is required for YAML export. Install with: uv pip install pyyaml"
            )

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)

    def validate_tool_paths(self, base_path: str | Path | None = None) -> list[str]:
        """Agent tool attachments are validated by name against the registry at runtime."""
        return []

    def validate_skill_paths(self, base_path: str | Path | None = None) -> list[str]:
        """Agent skill attachments are validated by name against the registry at runtime."""
        return []

    def validate_memory_paths(self, base_path: str | Path | None = None) -> list[str]:
        """Validate that memory file paths exist.

        Args:
            base_path: Optional base path for resolving relative paths.

        Returns:
            List of memory paths that failed validation.
        """
        failed: list[str] = []
        base = Path(base_path) if base_path else Path.cwd()

        for memory_path in self.memory:
            memory_file = base / memory_path
            if not memory_file.exists() or not memory_file.is_file():
                failed.append(memory_path)

        return failed

    def validate_all_paths(self, base_path: str | Path | None = None) -> dict[str, list[str]]:
        """Validate all paths in the agent definition.

        Args:
            base_path: Optional base path for resolving relative paths.

        Returns:
            Dictionary with keys 'tools', 'skills', 'memory' containing
            lists of paths that failed validation.
        """
        return {
            "tools": self.validate_tool_paths(base_path),
            "skills": self.validate_skill_paths(base_path),
            "memory": self.validate_memory_paths(base_path),
        }

    def _resolve_tools(self, base_path: str | Path | None = None) -> list[BaseTool]:
        """Direct tool path resolution is no longer supported.

        Agent definitions attach registry tool names only. Runtime resolution
        happens via ``RuntimeResolver.build_tools()``.
        """
        return []

    def to_subagent(self, base_path: str | Path | None = None) -> dict[str, Any]:
        """Translate AgentDefinition to Deep Agents SubAgent TypedDict.

        Args:
            base_path: Base path for resolving relative tool file paths. Should
                be the workspace root — not a per-session sandbox — because
                ``.cognition/tools/`` is a workspace-level concept loaded into
                the server process. See issue #112.

        Returns:
            A dict matching the Deep Agents SubAgent TypedDict specification:
            - name: str (required)
            - description: str (required)
            - system_prompt: str (required)
            - model: str | None (optional, format: "provider:model" or just "model")
            - tools: list[Any] | None (optional)
            - skills: list[str] | None (optional source paths)
            - middleware: list[Any] | None (optional)
            - interrupt_on: dict[str, InterruptOnConfig] | None (optional)
            - permissions: list[FilesystemPermission] | None (optional)
        """
        spec: dict[str, Any] = {
            "name": self.name,
            "description": self.description or "",
            "system_prompt": self.system_prompt,
        }

        if self.config.model:
            provider = self.config.provider
            if provider and provider != "openai_compatible":
                spec["model"] = f"{provider}:{self.config.model}"
            else:
                spec["model"] = self.config.model

        # Resolve tools from paths to BaseTool instances
        # This prevents AttributeError when ToolNode tries to access .name on strings
        resolved_tools = self._resolve_tools(base_path=base_path)
        if resolved_tools:
            spec["tools"] = resolved_tools

        if self.skills:
            spec["skills"] = ["/skills/api/"]

        if self.interrupt_on:
            spec["interrupt_on"] = {
                name: config.model_dump(exclude_none=True)
                for name, config in self.interrupt_on.items()
            }
        if self.permissions:
            from deepagents.middleware.filesystem import FilesystemPermission

            spec["permissions"] = [
                FilesystemPermission(**permission.model_dump())
                for permission in self.permissions
            ]

        return spec


def load_agent_definition(path: str | Path) -> AgentDefinition:
    """Load agent definition from YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Loaded AgentDefinition instance.

    Raises:
        ImportError: If PyYAML is not installed.
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML is invalid or missing required fields.
    """
    if not HAS_YAML:
        raise ImportError(
            "PyYAML is required for YAML loading. Install with: uv pip install pyyaml"
        )

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Agent definition file not found: {path}")

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(
            f"Invalid YAML content in {path}: expected dict, got {type(data).__name__}"
        )

    try:
        return AgentDefinition.model_validate(data)
    except Exception as e:
        raise ValueError(f"Failed to validate agent definition from {path}: {e}") from e


def load_agent_definition_from_markdown(path: str | Path) -> AgentDefinition:
    """Load agent definition from Markdown file with YAML frontmatter.

    The markdown file should have the format:
    ---
    description: Reviews code for best practices
    mode: subagent
    model: anthropic/claude-haiku-4
    temperature: 0.1
    skills:
      - my-skill-name
    ---
    You are a code reviewer. Focus on security...

    The filename stem becomes the agent name. The body after the frontmatter
    becomes the system_prompt.

    Args:
        path: Path to the markdown file.

    Returns:
        Loaded AgentDefinition instance.

    Raises:
        ImportError: If PyYAML is not installed.
        FileNotFoundError: If the file does not exist.
        ValueError: If the frontmatter is invalid.
    """
    if not HAS_YAML:
        raise ImportError(
            "PyYAML is required for markdown loading. Install with: uv pip install pyyaml"
        )

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Agent definition file not found: {path}")

    content = path.read_text()

    # Parse frontmatter (content between --- markers at start)
    frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(frontmatter_pattern, content, re.DOTALL)

    if not match:
        raise ValueError(
            f"Invalid markdown format in {path}: expected YAML frontmatter between --- markers"
        )

    frontmatter_text = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter in {path}: {e}") from e

    if not isinstance(frontmatter, dict):
        raise ValueError(
            f"Invalid frontmatter in {path}: expected dict, got {type(frontmatter).__name__}"
        )

    # Build AgentDefinition from frontmatter + body
    name = path.stem  # filename without extension

    legacy_a2a_fields = {"a2a_exposed", "a2a_public_interface_url"} & frontmatter.keys()
    if legacy_a2a_fields:
        fields = ", ".join(sorted(legacy_a2a_fields))
        raise ValueError(f"Use nested 'a2a' configuration instead of: {fields}")

    # Extract config fields from frontmatter
    config_kwargs: dict[str, Any] = {}
    if "temperature" in frontmatter:
        config_kwargs["temperature"] = frontmatter["temperature"]
    if "model" in frontmatter:
        # Model can be "provider/model" format or just model name
        model_value = frontmatter["model"]
        if "/" in model_value:
            provider, model = model_value.split("/", 1)
            config_kwargs["provider"] = provider
            config_kwargs["model"] = model
        else:
            config_kwargs["model"] = model_value

    config_block = frontmatter.get("config")
    if isinstance(config_block, dict):
        for key in (
            "temperature",
            "max_tokens",
            "recursion_limit",
            "tool_token_limit_before_evict",
            "context_policy",
            "blocked_tools",
            "excluded_tools",
            "provider",
            "model",
            "timeout_seconds",
            "sandbox_profile",
            "sandbox_execution_role_arn",
        ):
            if key in config_block:
                config_kwargs[key] = config_block[key]

    definition = AgentDefinition(
        name=name,
        display_name=frontmatter.get("display_name"),
        a2a=frontmatter.get("a2a", {}),
        system_prompt=body,
        description=frontmatter.get("description"),
        mode=frontmatter.get("mode", "all"),
        hidden=frontmatter.get("hidden", False),
        native=False,  # User-defined
        tools=frontmatter.get("tools", []),
        skills=frontmatter.get("skills", []),
        memory=frontmatter.get("memory", []),
        async_subagents=frontmatter.get("async_subagents", []),
        mcp=frontmatter.get("mcp", {}),
        config=AgentConfig(**config_kwargs),
    )

    return definition


__all__ = [
    "A2AConfig",
    "A2APublicInterfaceUrl",
    "A2APublicSkill",
    "AgentConfig",
    "AgentDefinition",
    "AgentMcpConfig",
    "AgentMcpServerConfig",
    "AgentSkillBundle",
    "AsyncSubagentConfig",
    "McpAuthConfig",
    "MCPAuthType",
    "McpNoAuthConfig",
    "McpOAuthConfig",
    "McpStaticBearerAuthConfig",
    "McpWorkloadTokenExchangeAuthConfig",
    "SubagentDefinition",
    "load_agent_definition",
    "load_agent_definition_from_markdown",
]
