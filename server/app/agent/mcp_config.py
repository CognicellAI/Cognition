"""Declarative, per-agent MCP configuration.

MCP transport configuration is intentionally a capability of an Agent
definition. It does not expose raw headers, credentials, callback code, or
model-controlled endpoint selection.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class McpAuthConfig(BaseModel):
    """A supported MCP transport authentication mode.

    Profiles are opaque deployment references. Cognition never persists a
    bearer value, provider credential, raw header, or authentication callback
    in an Agent definition.
    """

    type: Literal["none", "mcp_oauth", "workload_token_exchange", "static_bearer"] = "none"
    profile: str | None = Field(default=None, min_length=1, max_length=100)
    env: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("env")
    @classmethod
    def validate_env_name(cls, value: str | None) -> str | None:
        if value is not None and (not value.replace("_", "").isalnum() or value[0].isdigit()):
            raise ValueError("auth env must be an environment-variable name, not a token value")
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> McpAuthConfig:
        if self.type == "workload_token_exchange":
            if self.profile is None:
                raise ValueError("workload_token_exchange requires an opaque auth profile")
            if self.env is not None:
                raise ValueError("workload_token_exchange cannot use an environment token")
        elif self.type == "static_bearer":
            if self.env is None:
                raise ValueError("static_bearer requires an environment-variable name")
            if self.profile is not None:
                raise ValueError("static_bearer cannot use an auth profile")
        elif self.profile is not None or self.env is not None:
            raise ValueError(f"{self.type} authentication does not accept profile or env fields")
        return self


class AgentMcpServer(BaseModel):
    """One remote MCP service attached directly to an Agent definition."""

    alias: str = Field(..., min_length=1, max_length=100)
    url: str = Field(..., min_length=1, max_length=2048)
    required: bool = True
    transport: Literal["streamable_http"] = "streamable_http"
    auth: McpAuthConfig = Field(default_factory=McpAuthConfig)

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("MCP server alias must be alphanumeric with hyphens/underscores only")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP server URL must be an absolute HTTP or HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("MCP server URL must not contain credentials")
        return value

    @property
    def canonical_prefix(self) -> str:
        """Return the stable server portion of the canonical tool identity."""
        return self.alias


def canonical_mcp_tool_identity(server_alias: str, provider_tool_name: str) -> tuple[str, str]:
    """Return the canonical, model-independent identity for an MCP tool."""
    return (server_alias, provider_tool_name)
