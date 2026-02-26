"""Application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from server.app.models import PromptConfig


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Allow extra fields from old .env files
        populate_by_name=True,  # Allow setting fields by Python name or alias
    )

    # Server settings
    host: str = Field(default="127.0.0.1", alias="COGNITION_HOST")
    port: int = Field(default=8000, alias="COGNITION_PORT")
    log_level: str = Field(default="info", alias="COGNITION_LOG_LEVEL")

    # Workspace settings
    workspace_root: Path = Field(
        default=Path("."),
        alias="COGNITION_WORKSPACE_ROOT",
    )

    # LLM settings
    llm_provider: str = Field(
        default="mock",
        alias="COGNITION_LLM_PROVIDER",
    )
    llm_model: str = Field(default="gpt-4o", alias="COGNITION_LLM_MODEL")
    llm_temperature: float | None = Field(default=None, alias="COGNITION_LLM_TEMPERATURE")
    llm_max_tokens: int | None = Field(default=None, alias="COGNITION_LLM_MAX_TOKENS")
    # System prompt configuration with explicit type/value
    # type: "file" | "inline" | "mlflow"
    # value: prompt text, file name, or mlflow reference
    system_prompt: PromptConfig = Field(
        default_factory=lambda: PromptConfig(type="file", value="system"),
        alias="COGNITION_SYSTEM_PROMPT",
    )

    # OpenAI settings - use SecretStr to prevent accidental logging
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_api_base: str | None = Field(default=None, alias="OPENAI_API_BASE")

    # OpenAI Compatible settings
    openai_compatible_base_url: str | None = Field(
        default=None, alias="COGNITION_OPENAI_COMPATIBLE_BASE_URL"
    )
    openai_compatible_api_key: SecretStr = Field(
        default=SecretStr("sk-no-key-required"),
        alias="COGNITION_OPENAI_COMPATIBLE_API_KEY",
    )

    # Bedrock settings - use SecretStr for credentials
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: SecretStr | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: SecretStr | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    bedrock_model_id: str = Field(
        default="anthropic.claude-3-sonnet-20240229-v1:0",
        alias="COGNITION_BEDROCK_MODEL_ID",
    )

    # Ollama settings (for local testing)
    ollama_model: str = Field(default="llama3.2", alias="COGNITION_OLLAMA_MODEL")
    ollama_base_url: str = Field(
        default="http://localhost:11434", alias="COGNITION_OLLAMA_BASE_URL"
    )

    # Session settings
    max_sessions: int = Field(default=100, alias="COGNITION_MAX_SESSIONS")
    session_timeout_seconds: float = Field(
        default=3600.0,
        alias="COGNITION_SESSION_TIMEOUT_SECONDS",
    )

    # Rate limiting settings
    rate_limit_per_minute: int = Field(default=60, alias="COGNITION_RATE_LIMIT_PER_MINUTE")
    rate_limit_burst: int = Field(default=10, alias="COGNITION_RATE_LIMIT_BURST")

    # Observability settings
    otel_enabled: bool = Field(default=False, alias="COGNITION_OTEL_ENABLED")
    otel_endpoint: str | None = Field(default=None, alias="COGNITION_OTEL_ENDPOINT")
    metrics_port: int = Field(default=9090, alias="COGNITION_METRICS_PORT")

    # MLflow settings
    mlflow_enabled: bool = Field(default=False, alias="COGNITION_MLFLOW_ENABLED")
    mlflow_tracking_uri: str | None = Field(default=None, alias="COGNITION_MLFLOW_TRACKING_URI")
    mlflow_experiment_name: str | None = Field(
        default="cognition", alias="COGNITION_MLFLOW_EXPERIMENT_NAME"
    )

    # Prompt Registry settings
    prompt_source: Literal["local", "mlflow"] = Field(
        default="local", alias="COGNITION_PROMPT_SOURCE"
    )
    prompt_fallback_to_local: bool = Field(default=True, alias="COGNITION_PROMPT_FALLBACK_TO_LOCAL")
    prompts_dir: str | None = Field(default=".cognition/prompts", alias="COGNITION_PROMPTS_DIR")

    # CORS settings
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        alias="COGNITION_CORS_ORIGINS",
        description="Allowed CORS origins. Defaults to common dev ports.",
    )
    cors_methods: list[str] = Field(
        default=["GET", "POST", "PUT", "DELETE", "PATCH"],
        alias="COGNITION_CORS_METHODS",
    )
    cors_headers: list[str] = Field(
        default=["Content-Type", "Authorization"],
        alias="COGNITION_CORS_HEADERS",
    )
    cors_credentials: bool = Field(
        default=True,
        alias="COGNITION_CORS_CREDENTIALS",
    )

    @field_validator("cors_origins", "cors_methods", "cors_headers", "scope_keys", mode="before")
    @classmethod
    def parse_comma_separated_list(cls, v: Any) -> list[str] | Any:
        """Parse comma-separated string into list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",")]
        return v

    # Agent behavior
    agent_memory: list[str] = Field(default=["AGENTS.md"], alias="COGNITION_AGENT_MEMORY")
    agent_skills: list[str] = Field(default=[".cognition/skills/"], alias="COGNITION_AGENT_SKILLS")
    agent_subagents: list[dict[str, Any]] = Field(default=[], alias="COGNITION_AGENT_SUBAGENTS")
    agent_interrupt_on: dict[str, bool] = Field(default={}, alias="COGNITION_AGENT_INTERRUPT_ON")

    # Persistence settings
    persistence_backend: Literal["sqlite", "memory", "postgres"] = Field(
        default="sqlite",
        alias="COGNITION_PERSISTENCE_BACKEND",
    )
    persistence_uri: str = Field(
        default=".cognition/state.db",
        alias="COGNITION_PERSISTENCE_URI",
    )

    # Sandbox / Execution backend settings
    sandbox_backend: Literal["local", "docker"] = Field(
        default="local",
        alias="COGNITION_SANDBOX_BACKEND",
    )
    docker_image: str = Field(
        default="cognition-sandbox:latest",
        alias="COGNITION_DOCKER_IMAGE",
    )
    docker_network: str = Field(
        default="none",
        alias="COGNITION_DOCKER_NETWORK",
    )
    docker_host_workspace: str = Field(
        default="",
        alias="COGNITION_DOCKER_HOST_WORKSPACE",
        description=(
            "Host filesystem path that maps to the container workspace. "
            "Required when Cognition runs inside Docker and spawns sibling "
            "sandbox containers — the sandbox mount must use the host path, "
            "not the container-internal path. Leave empty for local execution."
        ),
    )
    docker_timeout: float = Field(
        default=300.0,
        alias="COGNITION_DOCKER_TIMEOUT",
    )
    docker_memory_limit: str = Field(
        default="512m",
        alias="COGNITION_DOCKER_MEMORY_LIMIT",
    )
    docker_cpu_limit: float = Field(
        default=1.0,
        alias="COGNITION_DOCKER_CPU_LIMIT",
    )

    # Security settings
    tool_security: Literal["warn", "strict"] = Field(
        default="warn",
        alias="COGNITION_TOOL_SECURITY",
        description=(
            "Security level for loading tools from .cognition/tools/. "
            "'warn' logs violations but continues loading; 'strict' blocks loading."
        ),
    )
    protected_paths: list[str] = Field(
        default=[".cognition"],
        alias="COGNITION_PROTECTED_PATHS",
        description=(
            "List of paths that agents cannot write to or execute commands in. "
            "Paths are relative to the workspace root."
        ),
    )
    trusted_tool_namespaces: list[str] = Field(
        default=["server.app.tools"],
        alias="COGNITION_TRUSTED_TOOL_NAMESPACES",
        description=(
            "List of trusted namespaces for tool imports. "
            "Tools outside these namespaces will be blocked."
        ),
    )
    blocked_tools: list[str] = Field(
        default=[],
        alias="COGNITION_BLOCKED_TOOLS",
        description=(
            "List of tool names that are blocked from execution. "
            "Tool names are matched exactly (case-sensitive)."
        ),
    )

    # Session scoping settings
    scoping_enabled: bool = Field(
        default=False,
        alias="COGNITION_SCOPING_ENABLED",
    )
    scope_keys: list[str] = Field(
        default=["user"],
        alias="COGNITION_SCOPE_KEYS",
    )

    # SSE (Server-Sent Events) settings
    sse_retry_interval_ms: int = Field(
        default=3000,
        alias="COGNITION_SSE_RETRY_INTERVAL_MS",
    )
    sse_heartbeat_interval_seconds: float = Field(
        default=15.0,
        alias="COGNITION_SSE_HEARTBEAT_INTERVAL_SECONDS",
    )
    sse_buffer_size: int = Field(
        default=100,
        alias="COGNITION_SSE_BUFFER_SIZE",
    )

    # Test settings
    test_llm_mode: Literal["mock", "openai", "ollama"] = Field(
        default="mock",
        alias="COGNITION_TEST_LLM_MODE",
    )

    # MCP (Model Context Protocol) settings
    # Remote-only: Only HTTP/SSE connections supported
    mcp_servers: dict[str, Any] = Field(
        default_factory=dict,
        alias="COGNITION_MCP_SERVERS",
        description="Remote MCP server configurations. Only HTTP/SSE URLs allowed.",
    )

    @property
    def workspace_path(self) -> Path:
        """Get the current workspace path.

        Returns:
            Absolute path to the current working directory (server's workspace).
            This follows the git-style model where workspace is determined by
            where the server was started, or explicitly configured via environment.
        """
        return self.workspace_root.resolve()

    @field_validator("workspace_root")
    @classmethod
    def validate_workspace_root(cls, v: Path) -> Path:
        """Ensure workspace_root is an absolute path."""
        if not v.is_absolute():
            v = v.resolve()
        return v

    @field_validator("port", "metrics_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port number is in valid range."""
        if not 1 <= v <= 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("max_sessions")
    @classmethod
    def validate_max_sessions(cls, v: int) -> int:
        """Validate max_sessions is positive."""
        if v < 1:
            raise ValueError(f"max_sessions must be at least 1, got {v}")
        return v

    @field_validator("session_timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        """Validate timeout is positive."""
        if v <= 0:
            raise ValueError(f"session_timeout_seconds must be positive, got {v}")
        return v

    def get_llm_model(self) -> Any:
        """Get the LLM model instance based on provider."""
        from dataclasses import dataclass

        from server.app.llm.registry import get_provider_factory

        @dataclass
        class SimpleConfig:
            model: str
            api_key: str | None = None
            base_url: str | None = None
            region: str | None = None

        factory = get_provider_factory(self.llm_provider)
        config = SimpleConfig(model=self.llm_model)
        return factory(config, self)


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the global settings instance.

    Loads configuration from YAML files first, then environment variables.
    YAML config files (in order of precedence):
    1. ~/.cognition/config.yaml (global)
    2. .cognition/config.yaml (project-level)
    Environment variables override YAML config.
    """
    global _settings
    if _settings is None:
        # Load config from YAML files
        import os

        from server.app.config_loader import ConfigLoader

        loader = ConfigLoader()
        config_env_vars = loader.to_env_vars()

        # Set config file values as env vars (if not already set)
        # Environment variables take precedence
        for key, value in config_env_vars.items():
            if key not in os.environ:
                os.environ[key] = value

        # Create settings (reads from env vars)
        _settings = Settings()
    return _settings
