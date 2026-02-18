"""Declarative agent configuration for Cognition.

This module defines Pydantic models for declarative agent configuration,
enabling agent definitions via YAML files. This supports the P1-5 roadmap item
for Declarative AgentDefinition.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

try:
    import yaml  # type: ignore[import-untyped]

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class AgentConfig(BaseModel):
    """Agent runtime configuration.

    Attributes:
        temperature: Sampling temperature for LLM (0.0-2.0).
        max_tokens: Maximum tokens to generate.
        provider: LLM provider to use (mock, openai, bedrock, etc.).
        model: Model name to use.
        timeout_seconds: Request timeout in seconds.
    """

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)
    timeout_seconds: float | None = Field(default=None, gt=0)


class SubagentDefinition(BaseModel):
    """Definition of a subagent.

    Subagents are specialized agents that handle specific tasks
    within the context of a parent agent.

    Attributes:
        name: Unique name for the subagent.
        system_prompt: System prompt for the subagent.
        tools: Tool module paths available to this subagent.
        config: Runtime configuration overrides.
    """

    name: str = Field(..., min_length=1, max_length=100)
    system_prompt: str = Field(..., min_length=1)
    tools: list[str] = Field(default_factory=list)
    config: AgentConfig | None = Field(default=None)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate subagent name format."""
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"Subagent name must be alphanumeric with hyphens/underscores only: {v}"
            )
        return v


class AgentDefinition(BaseModel):
    """Declarative agent definition.

    This model defines a complete agent configuration including tools,
    skills, memory, subagents, and runtime configuration. It enables
    agents to be defined entirely via YAML configuration files.

    Attributes:
        name: Unique agent identifier.
        system_prompt: System prompt that defines agent behavior.
        tools: List of tool module paths (e.g., "server.app.tools.file_tools").
        skills: List of skill directory paths.
        memory: List of memory file paths.
        subagents: Nested subagent definitions.
        interrupt_on: Tools requiring human confirmation (tool_name -> bool).
        middleware: Middleware class paths.
        config: Runtime configuration (temperature, max_tokens, etc.).
    """

    name: str = Field(..., min_length=1, max_length=100)
    system_prompt: str = Field(..., min_length=1)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    memory: list[str] = Field(default_factory=list)
    subagents: list[SubagentDefinition] = Field(default_factory=list)
    interrupt_on: dict[str, bool] = Field(default_factory=dict)
    middleware: list[str] = Field(default_factory=list)
    config: AgentConfig = Field(default_factory=AgentConfig)

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
        """Validate tool module paths."""
        for tool_path in v:
            if not tool_path:
                raise ValueError("Tool path cannot be empty")
            # Basic validation: check it looks like a module path
            parts = tool_path.split(".")
            if len(parts) < 2:
                raise ValueError(
                    f"Tool path must be a valid Python module path (e.g., 'module.submodule'): {tool_path}"
                )
        return v

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, v: list[str]) -> list[str]:
        """Validate skill directory paths."""
        for skill_path in v:
            if not skill_path:
                raise ValueError("Skill path cannot be empty")
        return v

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
    def validate_middleware(cls, v: list[str]) -> list[str]:
        """Validate middleware class paths."""
        for middleware_path in v:
            if not middleware_path:
                raise ValueError("Middleware path cannot be empty")
            parts = middleware_path.split(".")
            if len(parts) < 2:
                raise ValueError(
                    f"Middleware path must be a valid Python class path: {middleware_path}"
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
        """Validate that tool module paths can be resolved.

        Args:
            base_path: Optional base path for resolving relative imports.

        Returns:
            List of tool paths that failed validation.
        """
        failed: list[str] = []

        for tool_path in self.tools:
            try:
                # Try to import the module
                spec = importlib.util.find_spec(tool_path)
                if spec is None:
                    failed.append(tool_path)
            except (ImportError, ModuleNotFoundError, ValueError):
                failed.append(tool_path)

        return failed

    def validate_skill_paths(self, base_path: str | Path | None = None) -> list[str]:
        """Validate that skill directory paths exist.

        Args:
            base_path: Optional base path for resolving relative paths.

        Returns:
            List of skill paths that failed validation.
        """
        failed: list[str] = []
        base = Path(base_path) if base_path else Path.cwd()

        for skill_path in self.skills:
            skill_dir = base / skill_path
            if not skill_dir.exists() or not skill_dir.is_dir():
                failed.append(skill_path)

        return failed

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


def create_default_agent_definition(name: str = "default-agent") -> AgentDefinition:
    """Create a default agent definition.

    Args:
        name: Name for the agent.

    Returns:
        AgentDefinition with sensible defaults.
    """
    return AgentDefinition(
        name=name,
        system_prompt="You are a helpful AI coding assistant.",
        tools=[],
        skills=[".cognition/skills/"],
        memory=["AGENTS.md"],
        subagents=[],
        interrupt_on={},
        middleware=[],
        config=AgentConfig(),
    )
