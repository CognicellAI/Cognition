"""Application settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpWorkloadTokenExchangeProfile(BaseModel):
    """Deployment-owned OAuth token-exchange profile for MCP transport."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth_token_exchange"] = "oauth_token_exchange"
    token_endpoint: str
    subject_token_source: Literal["workload_identity"] = "workload_identity"
    subject_token_type: str = "urn:ietf:params:oauth:token-type:access_token"
    audience: str = Field(min_length=1)
    client_auth: Literal["none", "client_secret_basic"] = "none"
    client_id: str | None = None
    client_secret_env: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @field_validator("token_endpoint")
    @classmethod
    def validate_token_endpoint(cls, value: str) -> str:
        """Require an absolute HTTP(S) token endpoint without URL credentials."""
        from urllib.parse import urlsplit

        if not value or any(character.isspace() for character in value):
            raise ValueError("MCP token endpoint must not be empty or contain whitespace")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("MCP token endpoint is malformed") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
            raise ValueError("MCP token endpoint must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("MCP token endpoint must not contain credentials")
        if parsed.fragment:
            raise ValueError("MCP token endpoint must not contain a fragment")
        return value

    @field_validator("audience")
    @classmethod
    def validate_audience(cls, value: str) -> str:
        """Reject ambiguous audience values while allowing URI-shaped identifiers."""
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("MCP token-exchange audience must not contain whitespace")
        return value

    @model_validator(mode="after")
    def validate_client_auth(self) -> McpWorkloadTokenExchangeProfile:
        """Require bounded environment client auth fields as one complete unit."""
        if self.client_auth == "client_secret_basic":
            if not self.client_id or self.client_secret_env is None:
                raise ValueError("client_secret_basic requires client_id and client_secret_env")
        elif self.client_id is not None or self.client_secret_env is not None:
            raise ValueError("client_id/client_secret_env require client_secret_basic")
        return self


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
    log_format: Literal["json", "console"] = Field(
        default="json",
        alias="COGNITION_LOG_FORMAT",
        description="Structured log renderer. Use 'console' for local development.",
    )
    deployment_mode: Literal["local", "development", "production"] = Field(
        default="development",
        alias="COGNITION_DEPLOYMENT_MODE",
        description=(
            "Builder-defined deployment label for operational metadata. Cognition does not "
            "infer storage or MCP authentication policy from this value."
        ),
    )

    # MCP transport identity is deployment configuration. Profiles contain no
    # subject token or provider credential and are referenced opaquely by Agent
    # definitions using workload_token_exchange authentication.
    mcp_auth_profiles: dict[str, McpWorkloadTokenExchangeProfile] = Field(
        default_factory=dict,
        alias="COGNITION_MCP_AUTH_PROFILES",
    )
    mcp_workload_identity_token_file: Path | None = Field(
        default=None,
        alias="COGNITION_MCP_WORKLOAD_IDENTITY_TOKEN_FILE",
        description="Projected, rotating workload subject-token file.",
    )
    mcp_workload_identity_token: SecretStr | None = Field(
        default=None,
        alias="COGNITION_MCP_WORKLOAD_IDENTITY_TOKEN",
        description="Environment fallback for the ambient workload subject token.",
    )

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
    otel_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "COGNITION_TRACING_ENABLED",
            "COGNITION_OTEL_ENABLED",
        ),
        description=(
            "Enable OpenTelemetry tracing. COGNITION_OTEL_ENABLED remains a compatibility alias."
        ),
    )
    otel_endpoint: str | None = Field(
        default=None,
        alias="COGNITION_OTLP_ENDPOINT",
        validation_alias=AliasChoices("COGNITION_OTLP_ENDPOINT", "COGNITION_OTEL_ENDPOINT"),
        description=(
            "Canonical OTLP collector endpoint. COGNITION_OTEL_ENDPOINT remains a "
            "compatibility alias."
        ),
    )
    otel_max_export_bytes: int = Field(
        default=3_670_016,
        ge=65_536,
        validation_alias=AliasChoices(
            "COGNITION_OTLP_MAX_EXPORT_BYTES",
            "COGNITION_OTEL_MAX_EXPORT_BYTES",
        ),
        description="Maximum encoded OTLP trace request size (default 3.5 MiB).",
    )
    otlp_queue_size: int = Field(
        default=2048,
        ge=1,
        alias="COGNITION_OTLP_QUEUE_SIZE",
        description="Maximum queued trace spans for the bounded OTLP exporter.",
    )
    otlp_export_timeout_ms: int = Field(
        default=30_000,
        ge=1,
        alias="COGNITION_OTLP_EXPORT_TIMEOUT_MS",
        description="Per-attempt OTLP trace export timeout in milliseconds.",
    )
    trace_sample_ratio: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        alias="COGNITION_TRACE_SAMPLE_RATIO",
        description="Parent-based root trace sample ratio for normal runs.",
    )
    trace_detail: Literal["standard", "debug"] = Field(
        default="standard",
        alias="COGNITION_TRACE_DETAIL",
        description="Trace detail profile. Standard removes routine framework hook noise.",
    )
    observability_scope_hmac_key: SecretStr | None = Field(
        default=None,
        alias="COGNITION_OBSERVABILITY_SCOPE_HMAC_KEY",
        description="Optional operator key for future scope fingerprinting; raw scopes are never emitted.",
    )
    otlp_metric_export_interval_ms: int = Field(
        default=60_000,
        ge=1_000,
        alias="COGNITION_OTLP_METRIC_EXPORT_INTERVAL_MS",
        description="OTLP metric export interval. Local Compose may override this for smoke tests.",
    )
    metrics_enabled: bool = Field(default=True, alias="COGNITION_METRICS_ENABLED")
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

    @field_validator(
        "cors_origins",
        "scope_keys",
        "callback_allowed_origins",
        mode="before",
    )
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

    # Durable Deep Agents file backend. Builders select local or S3-compatible
    # placement explicitly; Cognition never changes backends based on a
    # deployment label or after a selected-backend failure.
    durable_file_backend: Literal["local", "s3"] = Field(
        default="local",
        alias="COGNITION_DURABLE_FILE_BACKEND",
    )
    s3_bucket: str | None = Field(default=None, alias="COGNITION_S3_BUCKET")
    s3_prefix: str = Field(default="cognition", alias="COGNITION_S3_PREFIX")
    s3_endpoint_url: str | None = Field(default=None, alias="COGNITION_S3_ENDPOINT_URL")
    s3_region: str | None = Field(default=None, alias="COGNITION_S3_REGION")
    s3_force_path_style: bool = Field(
        default=False,
        alias="COGNITION_S3_FORCE_PATH_STYLE",
        description="Use path-style S3 requests for Garage and similar compatible stores.",
    )
    s3_scope_hmac_key: SecretStr | None = Field(
        default=None,
        alias="COGNITION_S3_SCOPE_HMAC_KEY",
        description=(
            "HMAC key used to derive opaque, exact-scope object prefixes. "
            "It is never persisted or exposed to agent runtime data."
        ),
    )

    # Sandbox / Execution backend settings
    sandbox_backend: Literal["local", "docker", "kubernetes", "aws_lambda_microvm"] = Field(
        default="local",
        alias="COGNITION_SANDBOX_BACKEND",
    )
    unsafe_local_execution: bool = Field(
        default=False,
        alias="COGNITION_ALLOW_UNSAFE_LOCAL_EXECUTION",
        description="Explicitly permit host-local execution for standalone development.",
    )
    allow_host_tools: bool = Field(
        default=False,
        alias="COGNITION_ALLOW_HOST_TOOLS",
        description="Explicitly inject Browser/Search/package host tools in development.",
    )
    allow_api_python_tools: bool = Field(
        default=False,
        alias="COGNITION_ALLOW_API_PYTHON_TOOLS",
        description="Explicitly permit host loading of API Python tool code in development.",
    )
    callback_allowed_origins: list[str] = Field(
        default_factory=list,
        alias="COGNITION_CALLBACK_ALLOWED_ORIGINS",
        description="Operator-approved HTTPS origins for per-message callbacks.",
    )
    agent_cache_max_entries: int = Field(
        default=128,
        ge=1,
        alias="COGNITION_AGENT_CACHE_MAX_ENTRIES",
    )
    agent_cache_ttl_seconds: float = Field(
        default=900.0,
        gt=0,
        alias="COGNITION_AGENT_CACHE_TTL_SECONDS",
    )
    session_service_cache_max_entries: int = Field(
        default=256,
        ge=1,
        alias="COGNITION_SESSION_SERVICE_CACHE_MAX_ENTRIES",
    )
    session_service_cache_ttl_seconds: float = Field(
        default=1800.0,
        gt=0,
        alias="COGNITION_SESSION_SERVICE_CACHE_TTL_SECONDS",
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

    # Kubernetes sandbox settings (only used when sandbox_backend="kubernetes")
    k8s_sandbox_template: str = Field(
        default="cognition-sandbox",
        alias="COGNITION_K8S_SANDBOX_TEMPLATE",
        description="SandboxTemplate CR name for creating K8s sandbox pods.",
    )
    k8s_sandbox_namespace: str = Field(
        default="default",
        alias="COGNITION_K8S_SANDBOX_NAMESPACE",
        description="Kubernetes namespace for sandbox CRs.",
    )
    k8s_sandbox_router_url: str = Field(
        default="http://sandbox-router-svc.default.svc.cluster.local:8080",
        alias="COGNITION_K8S_SANDBOX_ROUTER_URL",
        description="URL of the sandbox-router service for in-cluster communication.",
    )
    k8s_sandbox_ttl: int = Field(
        default=3600,
        alias="COGNITION_K8S_SANDBOX_TTL",
        description="Time-to-live in seconds for K8s sandbox CRs.",
    )
    k8s_sandbox_warm_pool: str | None = Field(
        default=None,
        alias="COGNITION_K8S_SANDBOX_WARM_POOL",
        description="Optional SandboxWarmPool CR name for pre-warmed sandbox allocation.",
    )

    # AWS Lambda MicroVM sandbox settings (only used when sandbox_backend="aws_lambda_microvm")
    aws_lambda_microvm_default_profile: str = Field(
        default="default",
        alias="COGNITION_AWS_LAMBDA_MICROVM_DEFAULT_PROFILE",
        description="Default SandboxProfile name for the AWS Lambda MicroVM backend.",
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

    # A2A protocol adapter
    a2a_enabled: bool = Field(
        default=True,
        alias="COGNITION_A2A_ENABLED",
        description=(
            "Enable the A2A (Agent-to-Agent) protocol adapter. "
            "When true, mounts /.well-known/agent-card.json and /a2a/{agent_name} endpoints. "
            "Individual agents still require a2a.exposed=true to be visible."
        ),
    )
    a2a_security_schemes: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        alias="COGNITION_A2A_SECURITY_SCHEMES",
        description=(
            "Canonical A2A ProtoJSON map of public Agent Card authentication schemes. "
            "This is discovery metadata only and must never contain credentials."
        ),
    )
    a2a_security_requirements: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="COGNITION_A2A_SECURITY_REQUIREMENTS",
        description=(
            "Canonical A2A ProtoJSON security requirements applied to generated Agent Cards."
        ),
    )
    a2a_max_raw_part_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        alias="COGNITION_A2A_MAX_RAW_PART_BYTES",
        description="Maximum decoded size accepted for one inbound A2A raw Part.",
    )
    a2a_max_parts: int = Field(default=64, ge=1, alias="COGNITION_A2A_MAX_PARTS")
    a2a_max_message_bytes: int = Field(
        default=16 * 1024 * 1024,
        ge=1,
        alias="COGNITION_A2A_MAX_MESSAGE_BYTES",
    )
    a2a_max_text_part_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1,
        alias="COGNITION_A2A_MAX_TEXT_PART_BYTES",
    )
    a2a_max_data_part_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1,
        alias="COGNITION_A2A_MAX_DATA_PART_BYTES",
    )
    a2a_max_output_artifacts: int = Field(
        default=100,
        ge=1,
        alias="COGNITION_A2A_MAX_OUTPUT_ARTIFACTS",
    )
    a2a_max_output_bytes: int = Field(
        default=16 * 1024 * 1024,
        ge=1,
        alias="COGNITION_A2A_MAX_OUTPUT_BYTES",
    )
    a2a_stream_chunk_bytes: int = Field(
        default=4096,
        ge=1,
        alias="COGNITION_A2A_STREAM_CHUNK_BYTES",
    )
    a2a_stream_flush_interval_seconds: float = Field(
        default=0.25,
        gt=0,
        alias="COGNITION_A2A_STREAM_FLUSH_INTERVAL_SECONDS",
    )
    a2a_terminal_task_ttl_seconds: int = Field(
        default=0,
        ge=0,
        alias="COGNITION_A2A_TERMINAL_TASK_TTL_SECONDS",
        description="Terminal A2A task retention. Zero disables automatic deletion.",
    )
    a2a_cleanup_interval_seconds: float = Field(
        default=3600.0,
        gt=0,
        alias="COGNITION_A2A_CLEANUP_INTERVAL_SECONDS",
    )
    a2a_cleanup_batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        alias="COGNITION_A2A_CLEANUP_BATCH_SIZE",
    )
    a2a_cleanup_grace_seconds: int = Field(
        default=300,
        ge=0,
        alias="COGNITION_A2A_CLEANUP_GRACE_SECONDS",
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

    @property
    def s3_enabled(self) -> bool:
        """Return whether durable file data is configured for S3-compatible storage."""
        return self.durable_file_backend == "s3"

    def validate_deployment_storage_policy(self) -> None:
        """Validate the builder-selected storage backend without classifying it."""
        if self.s3_enabled and not self.s3_bucket:
            raise ValueError("COGNITION_S3_BUCKET is required when durable_file_backend=s3")
        if self.s3_enabled and self.s3_scope_hmac_key is None:
            raise ValueError("COGNITION_S3_SCOPE_HMAC_KEY is required when durable_file_backend=s3")

    def get_mcp_auth_profile(self, name: str) -> McpWorkloadTokenExchangeProfile:
        """Resolve one deployment-owned MCP auth profile by opaque name."""
        try:
            return self.mcp_auth_profiles[name]
        except KeyError as exc:
            raise ValueError(f"Unknown MCP authentication profile: {name}") from exc


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
