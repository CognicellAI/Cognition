"""Builder-installed MCP transport authentication boundary.

Cognition owns the call site and validates the returned transport shape. A
builder owns credential acquisition, identity-provider choices, and any
short-lived in-memory token cache.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx


@dataclass(frozen=True)
class OutboundAuthRequest:
    """Trusted data supplied to a builder auth provider for one MCP operation."""

    agent_identity: str
    runtime_snapshot: str
    server_alias: str
    trusted_context: Mapping[str, str]
    deadline: datetime | None


@dataclass(frozen=True)
class OutboundAuthResult:
    """Bounded transport authentication material for one MCP connection."""

    auth: httpx.Auth | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    expires_at: datetime | None = None


class OutboundAuthProvider(Protocol):
    """Resolve outbound authentication without exposing credentials to Cognition data."""

    async def get_auth(self, request: OutboundAuthRequest) -> OutboundAuthResult:
        """Return auth material or raise ``OutboundAuthError`` with a redacted category."""


class OutboundAuthError(RuntimeError):
    """A redacted outbound-authentication failure."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class RegisteredOutboundAuthProvider:
    """A provider plus its deployment-controlled header projection allowlist."""

    provider: OutboundAuthProvider
    allowed_headers: frozenset[str]


class OutboundAuthProviderRegistry:
    """In-memory registry populated by the embedding application at startup."""

    def __init__(self) -> None:
        self._providers: dict[str, RegisteredOutboundAuthProvider] = {}

    def register(
        self,
        profile: str,
        provider: OutboundAuthProvider,
        *,
        allowed_headers: frozenset[str] = frozenset(),
    ) -> None:
        """Install a provider for an opaque, deployment-owned profile."""
        normalized_headers = frozenset(header.lower() for header in allowed_headers)
        self._providers[profile] = RegisteredOutboundAuthProvider(
            provider=provider,
            allowed_headers=normalized_headers,
        )

    def resolve(self, profile: str) -> RegisteredOutboundAuthProvider:
        """Return an installed provider or fail closed without revealing configuration."""
        try:
            return self._providers[profile]
        except KeyError as exc:
            raise OutboundAuthError("auth_provider_unavailable") from exc


_default_registry = OutboundAuthProviderRegistry()


def get_outbound_auth_provider_registry() -> OutboundAuthProviderRegistry:
    """Return the process-local registry for embedding-time installation."""
    return _default_registry
