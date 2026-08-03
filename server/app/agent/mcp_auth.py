"""Mandatory MCP transport authentication and trusted-context projection."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Mapping
from pathlib import Path
from typing import Any

import httpx
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from pydantic import SecretStr

from server.app.settings import McpWorkloadTokenExchangeProfile, Settings
from server.app.storage.mcp_oauth import (
    EncryptedMcpOAuthTokenStorage,
    McpOAuthStateRepository,
    McpOAuthStorageError,
)

_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_CONTEXT_HEADER_PREFIX = "X-Cognition-"
_MAX_SCOPE_HEADER_BYTES = 8 * 1024
_MAX_CONTEXT_HEADER_BYTES = 16 * 1024
_MAX_CONTEXT_VALUE_BYTES = 8 * 1024


class McpAuthenticationError(RuntimeError):
    """Typed, redacted MCP authentication failure."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"MCP authentication failed: {category}")


class StaticBearerAuth(httpx.Auth):
    """Apply an environment-backed bearer token without exposing its value."""

    def __init__(self, token: SecretStr) -> None:
        self._token = token

    @classmethod
    def from_environment(cls, env_name: str) -> StaticBearerAuth:
        """Read a named environment variable at transport construction."""
        value = os.environ.get(env_name)
        if not value:
            raise McpAuthenticationError("static_bearer_unavailable")
        return cls(SecretStr(value))

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        yield request


class AmbientWorkloadIdentity:
    """Read a rotating projected workload token or an environment fallback."""

    def __init__(self, *, token_file: Path | None, token: SecretStr | None) -> None:
        if token_file is None and token is None:
            raise McpAuthenticationError("workload_identity_unavailable")
        self._token_file = token_file
        self._token = token

    @classmethod
    def from_settings(cls, settings: Settings) -> AmbientWorkloadIdentity:
        return cls(
            token_file=settings.mcp_workload_identity_token_file,
            token=settings.mcp_workload_identity_token,
        )

    async def get_subject_token(self) -> SecretStr:
        """Return the current ambient subject token, preferring a projected file."""
        if self._token_file is not None:
            try:
                value = await asyncio.to_thread(
                    self._token_file.read_text,
                    encoding="utf-8",
                )
            except OSError as exc:
                raise McpAuthenticationError("workload_identity_unavailable") from exc
            value = value.strip()
            if not value:
                raise McpAuthenticationError("workload_identity_unavailable")
            return SecretStr(value)
        if self._token is None:
            raise McpAuthenticationError("workload_identity_unavailable")
        return self._token


class WorkloadTokenExchangeAuth(httpx.Auth):
    """Exchange ambient workload identity for one short-lived audience token."""

    requires_request_body = True

    def __init__(
        self,
        *,
        profile: McpWorkloadTokenExchangeProfile,
        audience: str,
        identity: AmbientWorkloadIdentity,
        client_secret: SecretStr | None = None,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._profile = profile
        self._audience = audience
        self._identity = identity
        self._client_secret = client_secret
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=profile.timeout_seconds)
        )
        self._cached_token: SecretStr | None = None
        self._cached_until = 0.0
        self._lock = asyncio.Lock()

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._get_access_token()
        request.headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        yield request

    async def _get_access_token(self) -> SecretStr:
        now = time.monotonic()
        if self._cached_token is not None and now < self._cached_until:
            return self._cached_token

        async with self._lock:
            now = time.monotonic()
            if self._cached_token is not None and now < self._cached_until:
                return self._cached_token
            subject_token = await self._identity.get_subject_token()
            token, expires_in = await self._exchange(subject_token)
            self._cached_token = token
            if expires_in is None:
                self._cached_until = 0.0
            else:
                leeway = min(30.0, max(1.0, expires_in * 0.1))
                self._cached_until = time.monotonic() + max(0.0, expires_in - leeway)
            return token

    async def _exchange(self, subject_token: SecretStr) -> tuple[SecretStr, float | None]:
        form = {
            "grant_type": _TOKEN_EXCHANGE_GRANT,
            "subject_token": subject_token.get_secret_value(),
            "subject_token_type": self._profile.subject_token_type,
            "requested_token_type": _ACCESS_TOKEN_TYPE,
            "audience": self._audience,
        }
        auth: httpx.BasicAuth | None = None
        if self._profile.client_auth == "client_secret_basic":
            if self._profile.client_id is None or self._client_secret is None:
                raise McpAuthenticationError("token_exchange_client_auth_unavailable")
            auth = httpx.BasicAuth(
                self._profile.client_id,
                self._client_secret.get_secret_value(),
            )
        try:
            async with self._client_factory() as client:
                if auth is None:
                    response = await client.post(
                        self._profile.token_endpoint,
                        data=form,
                        headers={"Accept": "application/json"},
                    )
                else:
                    response = await client.post(
                        self._profile.token_endpoint,
                        data=form,
                        auth=auth,
                        headers={"Accept": "application/json"},
                    )
        except httpx.HTTPError as exc:
            raise McpAuthenticationError("token_exchange_unavailable") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise McpAuthenticationError("token_exchange_denied")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise McpAuthenticationError("token_exchange_invalid_response") from exc
        if not isinstance(payload, Mapping):
            raise McpAuthenticationError("token_exchange_invalid_response")
        access_token = payload.get("access_token")
        token_type = payload.get("token_type", "Bearer")
        if (
            not isinstance(access_token, str)
            or not access_token
            or str(token_type).lower() != "bearer"
        ):
            raise McpAuthenticationError("token_exchange_invalid_response")
        expires_raw = payload.get("expires_in")
        expires_in: float | None = None
        if expires_raw is not None:
            try:
                expires_in = float(expires_raw)
            except (TypeError, ValueError) as exc:
                raise McpAuthenticationError("token_exchange_invalid_response") from exc
            if expires_in <= 0:
                raise McpAuthenticationError("token_exchange_invalid_response")
        return SecretStr(access_token), expires_in


def resolve_workload_client_secret(
    profile: McpWorkloadTokenExchangeProfile,
) -> SecretStr | None:
    """Resolve optional deployment-owned token-endpoint client authentication."""
    if profile.client_auth == "none":
        return None
    if profile.client_secret_env is None:
        raise McpAuthenticationError("token_exchange_client_auth_unavailable")
    value = os.environ.get(profile.client_secret_env)
    if not value:
        raise McpAuthenticationError("token_exchange_client_auth_unavailable")
    return SecretStr(value)


def create_mcp_oauth_auth(
    *,
    settings: Settings,
    repository: McpOAuthStateRepository | None,
    agent_name: str,
    effective_scope: Mapping[str, str],
    canonical_server_uri: str,
) -> OAuthClientProvider:
    """Create the upstream MCP OAuth provider with exact-scope encrypted state."""
    if (
        repository is None
        or settings.mcp_oauth_encryption_key is None
        or settings.mcp_oauth_redirect_uri is None
    ):
        raise McpAuthenticationError("oauth_configuration_unavailable")
    try:
        storage = EncryptedMcpOAuthTokenStorage(
            repository=repository,
            encryption_key=settings.mcp_oauth_encryption_key,
            agent_name=agent_name,
            effective_scope=effective_scope,
            canonical_server_uri=canonical_server_uri,
        )
        metadata = OAuthClientMetadata.model_validate(
            {
                "redirect_uris": [settings.mcp_oauth_redirect_uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": settings.mcp_oauth_client_name,
            }
        )
        return OAuthClientProvider(
            server_url=canonical_server_uri,
            client_metadata=metadata,
            storage=storage,
            timeout=settings.mcp_oauth_timeout_seconds,
            client_metadata_url=settings.mcp_oauth_client_metadata_url,
        )
    except (McpOAuthStorageError, ValueError) as exc:
        category = (
            exc.category if isinstance(exc, McpOAuthStorageError) else "oauth_configuration_invalid"
        )
        raise McpAuthenticationError(category) from exc


def trusted_context_headers(
    *,
    agent_name: str,
    agent_revision: int,
    effective_scope: Mapping[str, str],
    server_alias: str,
    canonical_server_uri: str,
    runtime: object | None = None,
) -> dict[str, str]:
    """Build the fixed, versioned, model-invisible gateway context envelope."""
    scope_json = json.dumps(
        dict(sorted(effective_scope.items())),
        separators=(",", ":"),
        ensure_ascii=True,
    )
    if len(scope_json.encode("utf-8")) > _MAX_SCOPE_HEADER_BYTES:
        raise McpAuthenticationError("trusted_context_too_large")
    headers = {
        f"{_CONTEXT_HEADER_PREFIX}Context-Version": "1",
        f"{_CONTEXT_HEADER_PREFIX}Agent-ID": agent_name,
        f"{_CONTEXT_HEADER_PREFIX}Agent-Revision": str(agent_revision),
        f"{_CONTEXT_HEADER_PREFIX}Effective-Scope": scope_json,
        f"{_CONTEXT_HEADER_PREFIX}MCP-Server-Alias": server_alias,
        f"{_CONTEXT_HEADER_PREFIX}MCP-Server-URI": canonical_server_uri,
    }
    dynamic = _runtime_context_values(runtime)
    for header, value in (
        (f"{_CONTEXT_HEADER_PREFIX}Session-ID", dynamic.get("session_id")),
        (f"{_CONTEXT_HEADER_PREFIX}Run-ID", dynamic.get("run_id")),
        (f"{_CONTEXT_HEADER_PREFIX}Request-Deadline", dynamic.get("request_deadline")),
    ):
        if value is not None:
            headers[header] = str(value)
    total_bytes = 0
    for header, value in headers.items():
        value_bytes = value.encode("utf-8")
        if len(value_bytes) > _MAX_CONTEXT_VALUE_BYTES:
            raise McpAuthenticationError("trusted_context_too_large")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise McpAuthenticationError("trusted_context_invalid")
        total_bytes += len(header.encode("ascii")) + len(value_bytes)
    if total_bytes > _MAX_CONTEXT_HEADER_BYTES:
        raise McpAuthenticationError("trusted_context_too_large")
    return headers


def _runtime_context_values(runtime: object | None) -> dict[str, Any]:
    context = getattr(runtime, "context", None)
    config = getattr(runtime, "config", None)
    config_dict = config if isinstance(config, dict) else {}
    configurable_raw = config_dict.get("configurable")
    configurable = configurable_raw if isinstance(configurable_raw, dict) else {}

    def context_value(name: str) -> Any:
        if isinstance(context, dict):
            return context.get(name)
        return getattr(context, name, None) if context is not None else None

    thread_id = context_value("thread_id") or configurable.get("thread_id")
    return {
        "session_id": context_value("session_id") or configurable.get("session_id"),
        "run_id": config_dict.get("run_id") or configurable.get("run_id") or thread_id,
        "request_deadline": context_value("request_deadline"),
    }


class McpTrustedContextInterceptor:
    """Overwrite per-call headers with Cognition's fixed workload context."""

    def __init__(self, contexts: Mapping[str, Mapping[str, Any]]) -> None:
        self._contexts = dict(contexts)

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
    ) -> MCPToolCallResult:
        context = self._contexts.get(request.server_name)
        if context is None:
            raise McpAuthenticationError("trusted_context_unavailable")
        headers = trusted_context_headers(runtime=request.runtime, **context)
        return await handler(request.override(headers=headers))


__all__ = [
    "AmbientWorkloadIdentity",
    "McpAuthenticationError",
    "McpTrustedContextInterceptor",
    "StaticBearerAuth",
    "WorkloadTokenExchangeAuth",
    "create_mcp_oauth_auth",
    "resolve_workload_client_secret",
    "trusted_context_headers",
]
