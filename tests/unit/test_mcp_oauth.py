"""Tests for encrypted direct-MCP OAuth persistence."""

from __future__ import annotations

import sqlite3

import pytest
from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from server.app.agent.mcp_oauth import (
    EncryptedMcpOAuthTokenStorage,
    McpOAuthAuthorizationStore,
    McpOAuthPartition,
    record_mcp_oauth_callback,
)


def _store(tmp_path, *, scope: dict[str, str]) -> EncryptedMcpOAuthTokenStorage:
    return EncryptedMcpOAuthTokenStorage(
        persistence_backend="sqlite",
        persistence_uri="oauth.db",
        encryption_key=Fernet.generate_key().decode(),
        partition=McpOAuthPartition(
            agent_identity="support-agent",
            runtime_snapshot="revision-1",
            effective_scope=scope,
            server_url="https://mcp.example.test/github/",
        ),
        workspace_path=tmp_path,
    )


@pytest.mark.asyncio
async def test_token_storage_encrypts_tokens_and_dynamic_client_data(tmp_path) -> None:
    storage = _store(tmp_path, scope={"tenant": "acme"})
    token = OAuthToken(access_token="access-secret", refresh_token="refresh-secret")
    client = OAuthClientInformationFull(
        redirect_uris=[AnyUrl("https://cognition.example.test/mcp/oauth/callback")],
        client_id="registered-client",
        client_secret="client-secret",
    )

    await storage.set_tokens(token)
    await storage.set_client_info(client)

    assert await storage.get_tokens() == token
    assert await storage.get_client_info() == client
    raw = sqlite3.connect(tmp_path / "oauth.db").execute(
        "SELECT partition_id, token_ciphertext, client_ciphertext FROM mcp_oauth_credentials"
    ).fetchone()
    assert raw is not None
    assert all(secret not in " ".join(str(value) for value in raw) for secret in [
        "access-secret",
        "refresh-secret",
        "registered-client",
        "client-secret",
        "acme",
    ])


@pytest.mark.asyncio
async def test_token_storage_does_not_cross_exact_effective_scope(tmp_path) -> None:
    acme = _store(tmp_path, scope={"tenant": "acme"})
    globex = _store(tmp_path, scope={"tenant": "globex"})

    await acme.set_tokens(OAuthToken(access_token="acme-token"))

    assert (await acme.get_tokens()).access_token == "acme-token"  # type: ignore[union-attr]
    assert await globex.get_tokens() is None


@pytest.mark.asyncio
async def test_callback_is_one_time_and_cannot_cross_scope_partitions(tmp_path) -> None:
    key = Fernet.generate_key().decode()

    def storage(scope: dict[str, str]) -> EncryptedMcpOAuthTokenStorage:
        return EncryptedMcpOAuthTokenStorage(
            persistence_backend="sqlite",
            persistence_uri="oauth.db",
            encryption_key=key,
            partition=McpOAuthPartition(
                agent_identity="support-agent",
                runtime_snapshot="revision-1",
                effective_scope=scope,
                server_url="https://mcp.example.test/github",
            ),
            workspace_path=tmp_path,
        )

    acme, globex = storage({"tenant": "acme"}), storage({"tenant": "globex"})
    await McpOAuthAuthorizationStore(acme).register("opaque-state", ttl_seconds=60)

    assert await record_mcp_oauth_callback(
        persistence_backend="sqlite",
        persistence_uri="oauth.db",
        encryption_key=key,
        workspace_path=tmp_path,
        state="opaque-state",
        code="provider-code",
    )
    assert await McpOAuthAuthorizationStore(globex).consume_callback("opaque-state") is None
    assert await McpOAuthAuthorizationStore(acme).consume_callback("opaque-state") == "provider-code"
    assert await McpOAuthAuthorizationStore(acme).consume_callback("opaque-state") is None
    raw = sqlite3.connect(tmp_path / "oauth.db").execute(
        "SELECT state_id, partition_id, code_ciphertext FROM mcp_oauth_authorizations"
    ).fetchone()
    assert raw is not None
    assert "provider-code" not in " ".join(str(value) for value in raw)
