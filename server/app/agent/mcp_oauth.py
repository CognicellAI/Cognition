"""Encrypted persistence for direct MCP OAuth client state.

This module deliberately stores MCP OAuth tokens outside Agent definitions,
runtime projections, and object storage.  The database row is keyed by an
HMAC-derived partition so tenant scope and canonical provider URL are not
recoverable from normal database inspection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken
from mcp.client.auth import TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class McpOAuthStorageError(RuntimeError):
    """A redacted failure in durable direct-MCP OAuth storage."""


@dataclass(frozen=True)
class McpOAuthPartition:
    """Trusted dimensions that isolate one direct-MCP OAuth authorization."""

    agent_identity: str
    runtime_snapshot: str
    effective_scope: dict[str, str]
    server_url: str


def canonicalize_mcp_server_url(url: str) -> str:
    """Return a stable remote-MCP URL identity without credentials or fragments."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80):
        port = None
    netloc = hostname if port is None else f"{hostname}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


class EncryptedMcpOAuthTokenStorage(TokenStorage):
    """MCP SDK ``TokenStorage`` backed by SQLite or PostgreSQL-compatible DBs.

    It uses a deployment supplied Fernet key for ciphertext and derives the
    opaque partition identifier with a separate HMAC domain.  Only ciphertext,
    timestamps, and non-reversible partition identifiers reach the database.
    """

    def __init__(
        self,
        *,
        persistence_backend: str,
        persistence_uri: str,
        encryption_key: str,
        partition: McpOAuthPartition,
        workspace_path: Path | None = None,
    ) -> None:
        if persistence_backend not in {"sqlite", "postgres"}:
            raise McpOAuthStorageError("oauth_storage_unavailable")
        try:
            self._fernet = Fernet(encryption_key.encode())
            key_material = base64.urlsafe_b64decode(encryption_key.encode())
        except Exception as exc:
            raise McpOAuthStorageError("oauth_storage_unavailable") from exc
        payload = json.dumps(
            {
                "agent_identity": partition.agent_identity,
                "runtime_snapshot": partition.runtime_snapshot,
                "effective_scope": partition.effective_scope,
                "server_url": canonicalize_mcp_server_url(partition.server_url),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._partition_id = hmac.new(key_material, b"cognition-mcp-oauth-v1\0" + payload, hashlib.sha256).hexdigest()
        self._backend = persistence_backend
        self._uri = self._resolve_uri(persistence_uri, workspace_path)

    @staticmethod
    def _resolve_uri(uri: str, workspace_path: Path | None) -> str:
        if uri.startswith("sqlite:///"):
            uri = uri.removeprefix("sqlite:///")
        if uri.startswith("postgresql+asyncpg://"):
            return uri.replace("postgresql+asyncpg://", "postgresql://", 1)
        path = Path(uri)
        if not path.is_absolute() and workspace_path is not None:
            path = workspace_path / path
        return str(path)

    async def _sqlite_connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self._uri)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_oauth_credentials (
                partition_id TEXT PRIMARY KEY,
                token_ciphertext TEXT,
                client_ciphertext TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()
        return db

    def _encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise McpOAuthStorageError("oauth_storage_unavailable") from exc

    async def _get_sqlite(self, column: str) -> str | None:
        db = await self._sqlite_connect()
        try:
            cursor = await db.execute(
                f"SELECT {column} FROM mcp_oauth_credentials WHERE partition_id = ?",
                (self._partition_id,),
            )
            row = await cursor.fetchone()
            return str(row[0]) if row and row[0] is not None else None
        finally:
            await db.close()

    async def _set_sqlite(self, column: str, value: str) -> None:
        db = await self._sqlite_connect()
        try:
            await db.execute(
                f"""
                INSERT INTO mcp_oauth_credentials (partition_id, {column}) VALUES (?, ?)
                ON CONFLICT(partition_id) DO UPDATE SET {column} = excluded.{column},
                    updated_at = CURRENT_TIMESTAMP
                """,
                (self._partition_id, value),
            )
            await db.commit()
        finally:
            await db.close()

    async def _get_postgres(self, column: str) -> str | None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._uri) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_oauth_credentials (
                    partition_id TEXT PRIMARY KEY,
                    token_ciphertext TEXT,
                    client_ciphertext TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT {column} FROM mcp_oauth_credentials WHERE partition_id = %s",
                    (self._partition_id,),
                )
                row = await cursor.fetchone()
                return str(row[0]) if row and row[0] is not None else None

    async def _set_postgres(self, column: str, value: str) -> None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._uri) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_oauth_credentials (
                    partition_id TEXT PRIMARY KEY,
                    token_ciphertext TEXT,
                    client_ciphertext TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                f"""
                INSERT INTO mcp_oauth_credentials (partition_id, {column}) VALUES (%s, %s)
                ON CONFLICT(partition_id) DO UPDATE SET {column} = EXCLUDED.{column},
                    updated_at = NOW()
                """,
                (self._partition_id, value),
            )

    async def _get(self, column: str) -> str | None:
        if self._backend == "sqlite":
            return await self._get_sqlite(column)
        return await self._get_postgres(column)

    async def _set(self, column: str, value: str) -> None:
        if self._backend == "sqlite":
            await self._set_sqlite(column, value)
            return
        await self._set_postgres(column, value)

    async def get_tokens(self) -> OAuthToken | None:
        """Return the decrypted OAuth access/refresh-token bundle, if present."""
        ciphertext = await self._get("token_ciphertext")
        return OAuthToken.model_validate_json(self._decrypt(ciphertext)) if ciphertext else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Encrypt and save an OAuth token bundle."""
        await self._set("token_ciphertext", self._encrypt(tokens.model_dump_json()))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Return registered OAuth client information, if present."""
        ciphertext = await self._get("client_ciphertext")
        return (
            OAuthClientInformationFull.model_validate_json(self._decrypt(ciphertext))
            if ciphertext
            else None
        )

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Encrypt and save dynamic client-registration metadata."""
        await self._set("client_ciphertext", self._encrypt(client_info.model_dump_json()))
