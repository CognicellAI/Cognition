"""Application settings and configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=[
            Path(__file__).parent.parent.parent / ".env",  # /cognition/.env
            Path(__file__).parent.parent / ".env",  # /cognition/server/.env (override)
        ],
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server Configuration
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    log_level: str = Field(default="info", alias="LOG_LEVEL")
    debug: bool = Field(default=False, alias="DEBUG")

    # LLM Configuration
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_api_base: str | None = Field(default=None, alias="OPENAI_API_BASE")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    default_model: str = Field(default="gpt-4-turbo-preview", alias="DEFAULT_MODEL")
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")

    # AWS Bedrock Configuration
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = Field(default=None, alias="AWS_SESSION_TOKEN")
    aws_profile: str | None = Field(default=None, alias="AWS_PROFILE")
    bedrock_model_id: str = Field(
        default="anthropic.claude-3-sonnet-20240229-v1:0", alias="BEDROCK_MODEL_ID"
    )
    use_bedrock_iam_role: bool = Field(default=False, alias="USE_BEDROCK_IAM_ROLE")

    # Container Configuration
    docker_image: str = Field(
        default="opencode-agent:py", alias="DOCKER_IMAGE"
    )  # Legacy: for dev tools only
    agent_docker_image: str = Field(
        default="cognition-agent:latest", alias="AGENT_DOCKER_IMAGE"
    )  # Agent runtime image with LangGraph
    container_timeout: int = Field(default=300, alias="CONTAINER_TIMEOUT")
    container_memory_limit: str = Field(default="2g", alias="CONTAINER_MEMORY_LIMIT")
    container_cpu_limit: float = Field(default=1.0, alias="CONTAINER_CPU_LIMIT")

    # Workspace Configuration
    workspace_root: Path = Field(default=Path("./workspaces"), alias="WORKSPACE_ROOT")
    max_sessions: int = Field(default=100, alias="MAX_SESSIONS")

    # Project Configuration
    max_projects: int = Field(default=1000, alias="MAX_PROJECTS")
    project_cleanup_enabled: bool = Field(default=True, alias="PROJECT_CLEANUP_ENABLED")
    project_cleanup_after_days: int = Field(default=30, alias="PROJECT_CLEANUP_AFTER_DAYS")
    project_cleanup_warning_days: int = Field(default=3, alias="PROJECT_CLEANUP_WARNING_DAYS")
    project_cleanup_check_interval: int = Field(
        default=86400, alias="PROJECT_CLEANUP_CHECK_INTERVAL"
    )  # seconds

    # Memory Persistence Configuration
    memory_snapshot_enabled: bool = Field(default=True, alias="MEMORY_SNAPSHOT_ENABLED")
    memory_snapshot_interval: int = Field(default=300, alias="MEMORY_SNAPSHOT_INTERVAL")  # seconds

    # Container Lifecycle
    container_stop_on_disconnect: bool = Field(default=True, alias="CONTAINER_STOP_ON_DISCONNECT")
    container_recreate_on_reconnect: bool = Field(
        default=True, alias="CONTAINER_RECREATE_ON_RECONNECT"
    )

    # Backend Configuration (for CompositeBackend routes)
    # JSON format: {""/path/"": {"type": "filesystem|store|state", "root": "/path"}}
    agent_backend_routes: str | None = Field(default=None, alias="AGENT_BACKEND_ROUTES")

    # Observability - OpenTelemetry
    otel_enabled: bool = Field(
        default=False, alias="OTEL_ENABLED"
    )  # Master switch (auto-enabled if LangSmith or OTEL_ENDPOINT set)
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )  # Custom OTLP backend (Jaeger, Tempo, etc.)
    otel_exporter_otlp_headers: str | None = Field(
        default=None, alias="OTEL_EXPORTER_OTLP_HEADERS"
    )  # key=value,key2=value2 format
    otel_service_name: str = Field(default="cognition", alias="OTEL_SERVICE_NAME")

    # Observability - LangSmith (for Deep Agents native tracing)
    langsmith_tracing: bool = Field(
        default=False, alias="LANGSMITH_TRACING"
    )  # Enable LangSmith agent tracing
    langsmith_api_key: str | None = Field(
        default=None, alias="LANGSMITH_API_KEY"
    )  # Required if langsmith_tracing=true
    langsmith_project: str = Field(
        default="cognition", alias="LANGSMITH_PROJECT"
    )  # Project name in LangSmith
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT"
    )  # Change for self-hosted LangSmith

    @property
    def has_llm_config(self) -> bool:
        """Check if at least one LLM API key is configured."""
        # Standard providers
        if self.openai_api_key or self.anthropic_api_key:
            return True

        # AWS Bedrock
        if self.llm_provider == "bedrock":
            return True

        # OpenAI-compatible API (e.g., LiteLLM, vLLM, Ollama)
        # Requires either API key or base URL (some local instances don't need auth)
        if self.llm_provider == "openai_compatible":
            return bool(self.openai_api_base)

        return False

    @property
    def bedrock_has_credentials(self) -> bool:
        """Check if AWS Bedrock has credentials configured."""
        # IAM role doesn't require explicit credentials
        if self.use_bedrock_iam_role:
            return True
        # Check for explicit credentials
        return bool(self.aws_access_key_id and self.aws_secret_access_key) or bool(self.aws_profile)

    def get_workspace_path(self, session_id: str) -> Path:
        """Get the workspace path for a session."""
        return self.workspace_root / session_id


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
