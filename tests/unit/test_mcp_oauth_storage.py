"""Encrypted persistence tests for MCP OAuth SDK state."""

from __future__ import annotations

from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import SecretStr

from server.app.settings import Settings
from server.app.storage.factory import create_mcp_oauth_state_repository
from server.app.storage.mcp_oauth import (
    EncryptedMcpOAuthTokenStorage,
    McpOAuthStorageError,
    MemoryMcpOAuthStateRepository,
)


def _key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode("ascii"))


def _storage(
    repository: MemoryMcpOAuthStateRepository,
    key: SecretStr,
    *,
    scope: dict[str, str] | None = None,
    agent_name: str = "support-agent",
    uri: str = "https://mcp.example.test/github",
) -> EncryptedMcpOAuthTokenStorage:
    return EncryptedMcpOAuthTokenStorage(
        repository=repository,
        encryption_key=key,
        agent_name=agent_name,
        effective_scope=scope or {"tenant": "acme"},
        canonical_server_uri=uri,
    )


async def test_tokens_and_dynamic_client_secrets_are_encrypted_at_rest() -> None:
    repository = MemoryMcpOAuthStateRepository()
    storage = _storage(repository, _key())
    tokens = OAuthToken(
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_in=300,
    )
    client = OAuthClientInformationFull.model_validate(
        {
            "redirect_uris": ["https://cognition.example.test/mcp/oauth/callback"],
            "client_id": "dynamic-client",
            "client_secret": "client-secret",
        }
    )

    await storage.set_tokens(tokens)
    await storage.set_client_info(client)

    serialized_repository = str(repository._rows)  # noqa: SLF001 - ciphertext assertion
    assert "access-secret" not in serialized_repository
    assert "refresh-secret" not in serialized_repository
    assert "client-secret" not in serialized_repository
    assert await storage.get_tokens() == tokens
    assert await storage.get_client_info() == client


async def test_exact_scope_agent_and_server_partitions_do_not_cross_read() -> None:
    repository = MemoryMcpOAuthStateRepository()
    key = _key()
    source = _storage(repository, key)
    await source.set_tokens(OAuthToken(access_token="source-token"))

    different_scope = _storage(repository, key, scope={"tenant": "other"})
    different_agent = _storage(repository, key, agent_name="other-agent")
    different_server = _storage(repository, key, uri="https://mcp.example.test/slack")

    assert (await source.get_tokens()).access_token == "source-token"  # type: ignore[union-attr]
    assert await different_scope.get_tokens() is None
    assert await different_agent.get_tokens() is None
    assert await different_server.get_tokens() is None
    assert len(repository._rows) == 1  # noqa: SLF001 - partition assertion
    partition_key = next(iter(repository._rows))  # noqa: SLF001 - partition assertion
    assert len(partition_key) == 64
    assert "acme" not in partition_key


async def test_corrupt_ciphertext_fails_with_redacted_category() -> None:
    repository = MemoryMcpOAuthStateRepository()
    storage = _storage(repository, _key())
    await storage.set_tokens(OAuthToken(access_token="secret-token"))
    row = next(iter(repository._rows.values()))  # noqa: SLF001 - corruption fixture
    row["tokens_ciphertext"] = "not-valid-fernet-ciphertext"

    try:
        await storage.get_tokens()
    except McpOAuthStorageError as exc:
        assert exc.category == "state_unreadable"
        assert "secret-token" not in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("wrong encryption key unexpectedly decrypted OAuth state")


async def test_sqlite_repository_persists_only_encrypted_state(tmp_path) -> None:
    settings = Settings.model_validate(
        {
            "workspace_root": str(tmp_path),
            "persistence_backend": "sqlite",
            "persistence_uri": "oauth.db",
        }
    )
    key = _key()
    first_repository = create_mcp_oauth_state_repository(settings)
    await first_repository.initialize()
    first_storage = EncryptedMcpOAuthTokenStorage(
        repository=first_repository,
        encryption_key=key,
        agent_name="support-agent",
        effective_scope={"tenant": "acme"},
        canonical_server_uri="https://mcp.example.test/github",
    )
    await first_storage.set_tokens(OAuthToken(access_token="durable-secret"))
    await first_repository.close()

    database_bytes = (tmp_path / "oauth.db").read_bytes()
    assert b"durable-secret" not in database_bytes

    second_repository = create_mcp_oauth_state_repository(settings)
    await second_repository.initialize()
    second_storage = EncryptedMcpOAuthTokenStorage(
        repository=second_repository,
        encryption_key=key,
        agent_name="support-agent",
        effective_scope={"tenant": "acme"},
        canonical_server_uri="https://mcp.example.test/github",
    )
    restored = await second_storage.get_tokens()
    await second_repository.close()

    assert restored is not None
    assert restored.access_token == "durable-secret"
