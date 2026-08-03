"""Encrypted, exact-scope persistence for the upstream MCP OAuth client."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from server.app.storage.schema import mcp_oauth_state_table


class McpOAuthStorageError(RuntimeError):
    """Typed, redacted OAuth persistence failure."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"MCP OAuth storage failed: {category}")


class McpOAuthStateRepository(Protocol):
    """Opaque ciphertext repository used by the SDK storage adapter."""

    async def get_ciphertext(self, partition_key: str, field: str) -> str | None: ...

    async def set_ciphertext(self, partition_key: str, field: str, value: str) -> None: ...

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...


class MemoryMcpOAuthStateRepository:
    """Process-local OAuth state repository for memory-backed development."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, str]] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        self._rows.clear()

    async def get_ciphertext(self, partition_key: str, field: str) -> str | None:
        return self._rows.get(partition_key, {}).get(field)

    async def set_ciphertext(self, partition_key: str, field: str, value: str) -> None:
        self._rows.setdefault(partition_key, {})[field] = value


class SqlMcpOAuthStateRepository:
    """SQLAlchemy repository shared by SQLite and PostgreSQL deployments."""

    _FIELDS = frozenset({"tokens_ciphertext", "client_info_ciphertext"})

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: mcp_oauth_state_table.create(
                    sync_connection,
                    checkfirst=True,
                )
            )

    async def close(self) -> None:
        await self._engine.dispose()

    async def get_ciphertext(self, partition_key: str, field: str) -> str | None:
        column = self._column(field)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                select(column).where(mcp_oauth_state_table.c.partition_key == partition_key)
            )
        value = result.scalar_one_or_none()
        return str(value) if value is not None else None

    async def set_ciphertext(self, partition_key: str, field: str, value: str) -> None:
        self._column(field)
        dialect = self._engine.dialect.name
        values = {"partition_key": partition_key, field: value}
        if dialect == "sqlite":
            statement: Any = sqlite_insert(mcp_oauth_state_table).values(**values)
        elif dialect == "postgresql":
            statement = postgres_insert(mcp_oauth_state_table).values(**values)
        else:  # pragma: no cover - factory admits only supported SQL backends
            raise McpOAuthStorageError("backend_unsupported")
        statement = statement.on_conflict_do_update(
            index_elements=[mcp_oauth_state_table.c.partition_key],
            set_={field: value},
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    @classmethod
    def _column(cls, field: str) -> Any:
        if field not in cls._FIELDS:
            raise McpOAuthStorageError("field_invalid")
        return mcp_oauth_state_table.c[field]


class EncryptedMcpOAuthTokenStorage(TokenStorage):
    """MCP SDK token storage encrypted and partitioned by trusted runtime identity."""

    def __init__(
        self,
        *,
        repository: McpOAuthStateRepository,
        encryption_key: SecretStr,
        agent_name: str,
        effective_scope: Mapping[str, str],
        canonical_server_uri: str,
    ) -> None:
        try:
            key = encryption_key.get_secret_value().encode("ascii")
            self._fernet = Fernet(key)
        except (ValueError, UnicodeEncodeError) as exc:
            raise McpOAuthStorageError("encryption_key_invalid") from exc
        self._repository = repository
        self._partition_key = _partition_key(
            key=key,
            agent_name=agent_name,
            effective_scope=effective_scope,
            canonical_server_uri=canonical_server_uri,
        )

    async def get_tokens(self) -> OAuthToken | None:
        payload = await self._read("tokens_ciphertext")
        if payload is None:
            return None
        try:
            return OAuthToken.model_validate_json(payload)
        except ValidationError as exc:
            raise McpOAuthStorageError("state_invalid") from exc

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._write("tokens_ciphertext", tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        payload = await self._read("client_info_ciphertext")
        if payload is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate_json(payload)
        except ValidationError as exc:
            raise McpOAuthStorageError("state_invalid") from exc

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self._write("client_info_ciphertext", client_info.model_dump_json())

    async def _read(self, field: str) -> str | None:
        ciphertext = await self._repository.get_ciphertext(self._partition_key, field)
        if ciphertext is None:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise McpOAuthStorageError("state_unreadable") from exc

    async def _write(self, field: str, payload: str) -> None:
        ciphertext = self._fernet.encrypt(payload.encode("utf-8")).decode("ascii")
        await self._repository.set_ciphertext(self._partition_key, field, ciphertext)


def _partition_key(
    *,
    key: bytes,
    agent_name: str,
    effective_scope: Mapping[str, str],
    canonical_server_uri: str,
) -> str:
    canonical = json.dumps(
        {
            "agent_name": agent_name,
            "canonical_server_uri": canonical_server_uri,
            "effective_scope": dict(sorted(effective_scope.items())),
        },
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


__all__ = [
    "EncryptedMcpOAuthTokenStorage",
    "McpOAuthStateRepository",
    "McpOAuthStorageError",
    "MemoryMcpOAuthStateRepository",
    "SqlMcpOAuthStateRepository",
]
