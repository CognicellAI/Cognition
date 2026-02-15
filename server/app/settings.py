"""Application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Allow extra fields from old .env files
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
    llm_temperature: Optional[float] = Field(default=None, alias="COGNITION_LLM_TEMPERATURE")
    llm_max_tokens: Optional[int] = Field(default=None, alias="COGNITION_LLM_MAX_TOKENS")
    llm_system_prompt: Optional[str] = Field(default=None, alias="COGNITION_LLM_SYSTEM_PROMPT")

    # OpenAI settings - use SecretStr to prevent accidental logging
    openai_api_key: Optional[SecretStr] = Field(default=None, alias="OPENAI_API_KEY")
    openai_api_base: Optional[str] = Field(default=None, alias="OPENAI_API_BASE")

    # OpenAI Compatible settings
    openai_compatible_base_url: Optional[str] = Field(
        default=None, alias="COGNITION_OPENAI_COMPATIBLE_BASE_URL"
    )
    openai_compatible_api_key: SecretStr = Field(
        default=SecretStr("sk-no-key-required"),
        alias="COGNITION_OPENAI_COMPATIBLE_API_KEY",
    )

    # Bedrock settings - use SecretStr for credentials
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: Optional[SecretStr] = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[SecretStr] = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
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
    otel_endpoint: Optional[str] = Field(default=None, alias="COGNITION_OTEL_ENDPOINT")
    metrics_port: int = Field(default=9090, alias="COGNITION_METRICS_PORT")

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

    # Test settings
    test_llm_mode: Literal["mock", "openai", "ollama"] = Field(
        default="mock",
        alias="COGNITION_TEST_LLM_MODE",
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
            api_key: Optional[str] = None
            base_url: Optional[str] = None
            region: Optional[str] = None

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
        from server.app.config_loader import ConfigLoader
        import os

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
