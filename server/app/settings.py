"""Application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    This class covers *infrastructure* concerns only.  Agent/LLM/provider
    configuration has moved to the DB-backed ConfigRegistry (see
    server/app/storage/config_registry.py).

    Credentials (OPENAI_API_KEY, AWS_*) are read from environment variables
    at provider-factory time and are never stored here or in the DB.
    """

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

    # OpenAI credentials — read by provider factories, not used directly by Settings
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

    # AWS/Bedrock credentials — read by provider factories
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: SecretStr | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: SecretStr | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: SecretStr | None = Field(
        default=None,
        alias="AWS_SESSION_TOKEN",
        description=(
            "AWS session token for STS temporary credentials. "
            "Required when using short-lived credentials from sts:AssumeRole, "
            "AWS SSO, or CI/CD OIDC providers. Leave unset for static keys or "
            "ambient credentials (instance profile, ECS task role, etc.)."
        ),
    )
    bedrock_role_arn: str | None = Field(
        default=None,
        alias="COGNITION_BEDROCK_ROLE_ARN",
        description=(
            "Optional IAM role ARN for Cognition to assume via sts:AssumeRole before "
            "calling Bedrock. Useful for cross-account access or pinning exact permissions "
            "when running under docker-compose or any identity that already has "
            "sts:AssumeRole permission. Leave unset to use the ambient credential chain "
            "(instance profile, ECS task role, Lambda execution role, IRSA, etc.) directly."
        ),
    )

    # Rate limiting settings
    rate_limit_per_minute: int = Field(default=60, alias="COGNITION_RATE_LIMIT_PER_MINUTE")
    rate_limit_burst: int = Field(default=10, alias="COGNITION_RATE_LIMIT_BURST")

    # Observability settings
    otel_enabled: bool = Field(default=False, alias="COGNITION_OTEL_ENABLED")
    otel_endpoint: str | None = Field(default=None, alias="COGNITION_OTEL_ENDPOINT")
    metrics_port: int = Field(default=9090, alias="COGNITION_METRICS_PORT")

    # CORS settings
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        alias="COGNITION_CORS_ORIGINS",
        description="Allowed CORS origins. Defaults to common dev ports.",
    )
    cors_credentials: bool = Field(
        default=True,
        alias="COGNITION_CORS_CREDENTIALS",
    )

    @field_validator("cors_origins", "scope_keys", mode="before")
    @classmethod
    def parse_comma_separated_list(cls, v: Any) -> list[str] | Any:
        """Parse comma-separated string or JSON array into list.

        Supports both formats:
        - Comma-separated: "user,project"
        - JSON array: '["user", "project"]'
        """
        if isinstance(v, str):
            stripped = v.strip()
            # ISSUE-004: Accept JSON array syntax
            if stripped.startswith("["):
                import json

                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except json.JSONDecodeError:
                    pass  # Fall back to comma-separated parsing
            # Fall back to comma-separated
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v

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
    docker_memory_limit: str = Field(
        default="512m",
        alias="COGNITION_DOCKER_MEMORY_LIMIT",
    )
    docker_cpu_limit: float = Field(
        default=1.0,
        alias="COGNITION_DOCKER_CPU_LIMIT",
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

    # Model catalog settings
    model_catalog_url: str = Field(
        default="https://models.dev/api.json",
        alias="COGNITION_MODEL_CATALOG_URL",
        description=(
            "URL to fetch the model catalog JSON from. "
            "Defaults to the public models.dev catalog. "
            "Set to a local/mirror URL for air-gapped or self-hosted deployments."
        ),
    )
    model_catalog_ttl_seconds: int = Field(
        default=3600,
        alias="COGNITION_MODEL_CATALOG_TTL_SECONDS",
        description="How long (in seconds) to cache the model catalog in memory.",
    )

    # SSE (Server-Sent Events) settings
    sse_heartbeat_interval_seconds: float = Field(
        default=15.0,
        alias="COGNITION_SSE_HEARTBEAT_INTERVAL_SECONDS",
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

    @property
    def session_sandboxes_path(self) -> Path:
        """Return the root directory for per-session sandbox workspaces."""
        return self.workspace_path / ".cognition" / "sandboxes"

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
