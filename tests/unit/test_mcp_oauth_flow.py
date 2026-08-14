"""MCP OAuth authorization transaction tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthToken

from server.app.agent.mcp_oauth_flow import McpOAuthFlowCoordinator, McpOAuthFlowError
from server.app.settings import Settings
from server.app.storage.mcp_oauth import (
    EncryptedMcpOAuthTokenStorage,
    MemoryMcpOAuthStateRepository,
)


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "mcp_oauth_encryption_key": Fernet.generate_key().decode("ascii"),
            "mcp_oauth_redirect_uri": "https://cognition.example.test/mcp/oauth/callback",
            "mcp_oauth_timeout_seconds": 5,
        }
    )


class _SuccessfulOAuth(httpx.Auth):
    def __init__(
        self,
        *,
        redirect_handler: Callable[[str], Awaitable[None]],
        callback_handler: Callable[[], Awaitable[tuple[str, str | None]]],
        storage: EncryptedMcpOAuthTokenStorage,
    ) -> None:
        self._redirect_handler = redirect_handler
        self._callback_handler = callback_handler
        self._storage = storage

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        state = "sdk-generated-state"
        await self._redirect_handler(
            "https://identity.example.test/authorize?client_id=cognition&state=" + state
        )
        code, returned_state = await self._callback_handler()
        if code != "authorization-code" or returned_state != state:
            raise RuntimeError("invalid callback")
        await self._storage.set_tokens(OAuthToken(access_token="oauth-access-token"))
        request.headers["Authorization"] = "Bearer oauth-access-token"
        yield request


@pytest.mark.asyncio
async def test_authorization_flow_returns_url_and_completes_without_exposing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    repository = MemoryMcpOAuthStateRepository()

    def create_auth(**kwargs):
        storage = EncryptedMcpOAuthTokenStorage(
            repository=repository,
            encryption_key=settings.mcp_oauth_encryption_key,
            agent_name=kwargs["agent_name"],
            effective_scope=kwargs["effective_scope"],
            canonical_server_uri=kwargs["canonical_server_uri"],
        )
        return _SuccessfulOAuth(
            redirect_handler=kwargs["redirect_handler"],
            callback_handler=kwargs["callback_handler"],
            storage=storage,
        )

    monkeypatch.setattr(
        "server.app.agent.mcp_oauth_flow.create_mcp_oauth_auth",
        create_auth,
    )
    coordinator = McpOAuthFlowCoordinator(
        settings=settings,
        repository=repository,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    started = await coordinator.begin(
        agent_name="support-agent",
        server_alias="github",
        server_url="https://mcp.example.test/github",
        effective_scope={"tenant": "acme"},
    )
    assert started.status == "authorization_required"
    assert started.flow_id is not None
    assert started.authorization_url is not None
    assert "sdk-generated-state" in started.authorization_url
    assert "oauth-access-token" not in str(started)

    completed = await coordinator.complete(
        code="authorization-code",
        state="sdk-generated-state",
        effective_scope={"tenant": "acme"},
    )
    assert completed.status == "authorized"
    assert completed.authorization_url is None
    assert "oauth-access-token" not in str(completed)

    existing = await coordinator.begin(
        agent_name="support-agent",
        server_alias="github",
        server_url="https://mcp.example.test/github",
        effective_scope={"tenant": "acme"},
    )
    assert existing.status == "authorized"
    assert existing.flow_id is None
    await coordinator.close()


@pytest.mark.asyncio
async def test_authorization_callback_state_is_unknown_and_replay_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    repository = MemoryMcpOAuthStateRepository()

    def create_auth(**kwargs):
        return _SuccessfulOAuth(
            redirect_handler=kwargs["redirect_handler"],
            callback_handler=kwargs["callback_handler"],
            storage=EncryptedMcpOAuthTokenStorage(
                repository=repository,
                encryption_key=settings.mcp_oauth_encryption_key,
                agent_name=kwargs["agent_name"],
                effective_scope=kwargs["effective_scope"],
                canonical_server_uri=kwargs["canonical_server_uri"],
            ),
        )

    monkeypatch.setattr(
        "server.app.agent.mcp_oauth_flow.create_mcp_oauth_auth",
        create_auth,
    )
    coordinator = McpOAuthFlowCoordinator(
        settings=settings,
        repository=repository,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    with pytest.raises(McpOAuthFlowError) as unknown:
        await coordinator.complete(
            code="code",
            state="unknown",
            effective_scope={"tenant": "acme"},
        )
    assert unknown.value.category == "authorization_state_unknown"

    started = await coordinator.begin(
        agent_name="support-agent",
        server_alias="github",
        server_url="https://mcp.example.test/github",
        effective_scope={"tenant": "acme"},
    )
    with pytest.raises(McpOAuthFlowError) as cross_scope_callback:
        await coordinator.complete(
            code="authorization-code",
            state="sdk-generated-state",
            effective_scope={"tenant": "other"},
        )
    assert cross_scope_callback.value.category == "authorization_state_unknown"
    await coordinator.complete(
        code="authorization-code",
        state="sdk-generated-state",
        effective_scope={"tenant": "acme"},
    )
    with pytest.raises(McpOAuthFlowError) as replay:
        await coordinator.complete(
            code="authorization-code",
            state="sdk-generated-state",
            effective_scope={"tenant": "acme"},
        )
    assert replay.value.category == "authorization_callback_replayed"

    with pytest.raises(McpOAuthFlowError) as wrong_scope:
        coordinator.get(flow_id=started.flow_id or "", effective_scope={"tenant": "other"})
    assert wrong_scope.value.category == "authorization_flow_unknown"
    await coordinator.close()
