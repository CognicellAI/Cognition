"""ArtifactStore protocol and implementations.

Provides blob-store semantics over SQL for persistent, versioned,
scope-aware agent artifacts. Each version is an independent row —
the table is a flat key-value store, not a normalized relational model.

Design:
- ``ArtifactStore`` Protocol defines the async CRUD interface.
- ``SqliteArtifactStore`` uses aiosqlite.
- ``PostgresArtifactStore`` uses asyncpg.
- ``MemoryArtifactStore`` uses in-memory dicts (for tests).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from server.app.storage.common import effective_scope_key
from server.app.storage.config_models import ArtifactDefinition

logger = structlog.get_logger(__name__)

ARTIFACT_TYPE_VALUES = {
    "scratch",
    "artifact",
    "file",
    "contract",
    "eval",
    "memory",
    "policy",
}


def _scope_to_json(scope: dict[str, str] | None) -> str:
    return json.dumps(scope or {}, sort_keys=True)


def _scope_from_json(raw: str | dict[str, str] | None) -> dict[str, str]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _row_to_artifact(row: dict[str, Any]) -> ArtifactDefinition:
    return ArtifactDefinition(
        id=row["id"],
        version=row["version"],
        name=row["name"],
        artifact_type=row["artifact_type"],
        path=row.get("path") or "",
        content=row.get("content") or "",
        content_type=row.get("content_type") or "text/plain",
        object_key=row.get("object_key"),
        content_checksum=row.get("content_checksum"),
        content_size=row.get("content_size"),
        parent_version=row.get("parent_version"),
        run_id=row.get("run_id"),
        checkpoint_id=row.get("checkpoint_id"),
        visibility=row.get("visibility", "private") or "private",
        scope=_scope_from_json(row.get("scope")),
        source=row.get("source", "api") or "api",
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


@runtime_checkable
class ArtifactStore(Protocol):
    """Async CRUD interface for persistent agent artifacts.

    All methods are scope-aware. The natural key is (id, scope, version).
    """

    async def get_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        """Return the latest version of an artifact for the given scope."""
        ...

    async def list_artifacts(
        self,
        scope: dict[str, str] | None = None,
        artifact_type: str | None = None,
        run_id: str | None = None,
    ) -> list[ArtifactDefinition]:
        """List artifacts in scope, optionally filtered by type or run."""
        ...

    async def upsert_artifact(self, artifact: ArtifactDefinition) -> None:
        """Insert or replace an artifact version."""
        ...

    async def health_check(self) -> None:
        """Raise when the selected durable artifact storage is unavailable."""
        ...

    async def delete_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> bool:
        """Delete all versions of an artifact. Returns True if any deleted."""
        ...

    async def get_artifact_version(
        self, artifact_id: str, version: int, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        """Return a specific version of an artifact."""
        ...

    async def list_artifact_versions(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> list[ArtifactDefinition]:
        """List all versions of an artifact, ordered by version descending."""
        ...


# ───────────────────────────────────────────────────────────────────────────────
# SqliteArtifactStore
# ───────────────────────────────────────────────────────────────────────────────


class SqliteArtifactStore:
    """SQLite implementation of ArtifactStore."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: Any = None

    async def initialize(self) -> None:
        import aiosqlite

        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def health_check(self) -> None:
        """Verify the configured SQLite artifact database."""
        async with self._db.execute("SELECT 1") as cursor:
            await cursor.fetchone()

    async def get_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        exact_scope = scope or {}
        async with self._db.execute(
            """SELECT * FROM artifacts
               WHERE id = ? AND scope_key = ?
               ORDER BY version DESC LIMIT 1""",
            (artifact_id, effective_scope_key(exact_scope)),
        ) as cursor:
            row = await cursor.fetchone()
        artifact = _row_to_artifact(dict(row)) if row else None
        return artifact if artifact is not None and artifact.scope == exact_scope else None

    async def list_artifacts(
        self,
        scope: dict[str, str] | None = None,
        artifact_type: str | None = None,
        run_id: str | None = None,
    ) -> list[ArtifactDefinition]:
        exact_scope = scope or {}
        query = "SELECT * FROM artifacts WHERE scope_key = ?"
        params: list[Any] = [effective_scope_key(exact_scope)]

        if artifact_type is not None:
            query += " AND artifact_type = ?"
            params.append(artifact_type)
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)

        query += " ORDER BY version DESC"

        async with self._db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()

        seen: set[tuple[str, int]] = set()
        results: list[ArtifactDefinition] = []
        for row in rows:
            r = dict(row)
            if _scope_from_json(r.get("scope")) != exact_scope:
                continue
            key = (r["id"], r["version"])
            if key in seen:
                continue
            seen.add(key)
            results.append(_row_to_artifact(r))
        return results

    async def upsert_artifact(self, artifact: ArtifactDefinition) -> None:
        now = datetime.now(UTC)
        await self._db.execute(
            """INSERT OR REPLACE INTO artifacts
               (id, version, name, artifact_type, path, content, content_type,
                object_key, content_checksum, content_size,
                parent_version, run_id, checkpoint_id, visibility, scope,
                scope_key, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact.id,
                artifact.version,
                artifact.name,
                artifact.artifact_type,
                artifact.path,
                artifact.content,
                artifact.content_type,
                artifact.object_key,
                artifact.content_checksum,
                artifact.content_size,
                artifact.parent_version,
                artifact.run_id,
                artifact.checkpoint_id,
                artifact.visibility,
                _scope_to_json(artifact.scope),
                effective_scope_key(artifact.scope),
                artifact.source,
                artifact.created_at or now,
                artifact.updated_at or now,
            ),
        )
        await self._db.commit()

    async def delete_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> bool:
        exact_scope = scope or {}
        cursor = await self._db.execute(
            "DELETE FROM artifacts WHERE id = ? AND scope_key = ? AND scope = ?",
            (
                artifact_id,
                effective_scope_key(exact_scope),
                _scope_to_json(exact_scope),
            ),
        )
        await self._db.commit()
        return bool(cursor.rowcount and cursor.rowcount > 0)

    async def get_artifact_version(
        self, artifact_id: str, version: int, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        exact_scope = scope or {}
        async with self._db.execute(
            """
            SELECT * FROM artifacts
            WHERE id = ? AND scope_key = ? AND scope = ? AND version = ?
            """,
            (
                artifact_id,
                effective_scope_key(exact_scope),
                _scope_to_json(exact_scope),
                version,
            ),
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_artifact(dict(row)) if row else None

    async def list_artifact_versions(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> list[ArtifactDefinition]:
        exact_scope = scope or {}
        async with self._db.execute(
            """
            SELECT * FROM artifacts
            WHERE id = ? AND scope_key = ? AND scope = ?
            ORDER BY version DESC
            """,
            (
                artifact_id,
                effective_scope_key(exact_scope),
                _scope_to_json(exact_scope),
            ),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_artifact(dict(r)) for r in rows]


# ───────────────────────────────────────────────────────────────────────────────
# PostgresArtifactStore
# ───────────────────────────────────────────────────────────────────────────────


class PostgresArtifactStore:
    """PostgreSQL implementation of ArtifactStore."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def initialize(self) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(self._dsn, kwargs={"row_factory": dict_row})
        await self._pool.open()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def health_check(self) -> None:
        """Verify the configured PostgreSQL artifact database."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()

    async def get_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        exact_scope = scope or {}
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT * FROM artifacts
                       WHERE id = %s AND scope_key = %s AND scope = %s
                       ORDER BY version DESC LIMIT 1""",
                    (
                        artifact_id,
                        effective_scope_key(exact_scope),
                        _scope_to_json(exact_scope),
                    ),
                )
                row = await cur.fetchone()
        return _row_to_artifact(dict(row)) if row else None

    async def list_artifacts(
        self,
        scope: dict[str, str] | None = None,
        artifact_type: str | None = None,
        run_id: str | None = None,
    ) -> list[ArtifactDefinition]:
        exact_scope = scope or {}
        query = "SELECT * FROM artifacts WHERE scope_key = %s AND scope = %s"
        params: list[Any] = [
            effective_scope_key(exact_scope),
            _scope_to_json(exact_scope),
        ]

        if artifact_type is not None:
            query += " AND artifact_type = %s"
            params.append(artifact_type)
        if run_id is not None:
            query += " AND run_id = %s"
            params.append(run_id)

        query += " ORDER BY version DESC"

        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, tuple(params))
                rows = await cur.fetchall()

        seen: set[tuple[str, int]] = set()
        results: list[ArtifactDefinition] = []
        for row in rows:
            r = dict(row)
            key = (r["id"], r["version"])
            if key in seen:
                continue
            seen.add(key)
            results.append(_row_to_artifact(r))
        return results

    async def upsert_artifact(self, artifact: ArtifactDefinition) -> None:
        now = datetime.now(UTC)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO artifacts
                       (id, version, name, artifact_type, path, content, content_type,
                        object_key, content_checksum, content_size,
                        parent_version, run_id, checkpoint_id, visibility, scope,
                        scope_key, source, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id, scope_key, version) DO UPDATE SET
                        name = EXCLUDED.name,
                        artifact_type = EXCLUDED.artifact_type,
                        path = EXCLUDED.path,
                        content = EXCLUDED.content,
                        content_type = EXCLUDED.content_type,
                        object_key = EXCLUDED.object_key,
                        content_checksum = EXCLUDED.content_checksum,
                        content_size = EXCLUDED.content_size,
                        parent_version = EXCLUDED.parent_version,
                        run_id = EXCLUDED.run_id,
                        checkpoint_id = EXCLUDED.checkpoint_id,
                        visibility = EXCLUDED.visibility,
                        source = EXCLUDED.source,
                        updated_at = EXCLUDED.updated_at""",
                    (
                        artifact.id,
                        artifact.version,
                        artifact.name,
                        artifact.artifact_type,
                        artifact.path,
                        artifact.content,
                        artifact.content_type,
                        artifact.object_key,
                        artifact.content_checksum,
                        artifact.content_size,
                        artifact.parent_version,
                        artifact.run_id,
                        artifact.checkpoint_id,
                        artifact.visibility,
                        _scope_to_json(artifact.scope),
                        effective_scope_key(artifact.scope),
                        artifact.source,
                        artifact.created_at or now,
                        artifact.updated_at or now,
                    ),
                )

    async def delete_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> bool:
        exact_scope = scope or {}
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM artifacts
                    WHERE id = %s AND scope_key = %s AND scope = %s
                    """,
                    (
                        artifact_id,
                        effective_scope_key(exact_scope),
                        _scope_to_json(exact_scope),
                    ),
                )
                return cur.rowcount is not None and cur.rowcount > 0

    async def get_artifact_version(
        self, artifact_id: str, version: int, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        exact_scope = scope or {}
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE id = %s AND scope_key = %s AND scope = %s AND version = %s
                    """,
                    (
                        artifact_id,
                        effective_scope_key(exact_scope),
                        _scope_to_json(exact_scope),
                        version,
                    ),
                )
                row = await cur.fetchone()
        return _row_to_artifact(dict(row)) if row else None

    async def list_artifact_versions(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> list[ArtifactDefinition]:
        exact_scope = scope or {}
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE id = %s AND scope_key = %s AND scope = %s
                    ORDER BY version DESC
                    """,
                    (
                        artifact_id,
                        effective_scope_key(exact_scope),
                        _scope_to_json(exact_scope),
                    ),
                )
                rows = await cur.fetchall()
        return [_row_to_artifact(dict(r)) for r in rows]


# ───────────────────────────────────────────────────────────────────────────────
# MemoryArtifactStore
# ───────────────────────────────────────────────────────────────────────────────


class MemoryArtifactStore:
    """In-memory implementation of ArtifactStore (for tests)."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, int], dict[str, Any]] = {}

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def health_check(self) -> None:
        """The in-memory local-development store is always available."""
        return None

    def _key(self, artifact_id: str, scope: dict[str, str] | None) -> str:
        del artifact_id
        return effective_scope_key(scope)

    async def get_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        exact_scope = scope or {}
        scope_key = self._key(artifact_id, scope)
        best: tuple[int, dict[str, Any]] | None = None
        for (aid, sk, v), row in self._store.items():
            if (
                aid == artifact_id
                and sk == scope_key
                and _scope_from_json(row.get("scope")) == exact_scope
            ):
                if best is None or v > best[0]:
                    best = (v, row)
        return _row_to_artifact(best[1]) if best else None

    async def list_artifacts(
        self,
        scope: dict[str, str] | None = None,
        artifact_type: str | None = None,
        run_id: str | None = None,
    ) -> list[ArtifactDefinition]:
        exact_scope = scope or {}
        scope_key = self._key("", scope)
        results: list[ArtifactDefinition] = []
        seen: set[tuple[str, int]] = set()
        for (aid, sk, v), row in self._store.items():
            if (
                sk != scope_key
                or _scope_from_json(row.get("scope")) != exact_scope
            ):
                continue
            key = (aid, v)
            if key in seen:
                continue
            seen.add(key)
            if artifact_type is not None and row.get("artifact_type") != artifact_type:
                continue
            if run_id is not None and row.get("run_id") != run_id:
                continue
            results.append(_row_to_artifact(row))
        return results

    async def upsert_artifact(self, artifact: ArtifactDefinition) -> None:
        now = datetime.now(UTC)
        scope_key = self._key(artifact.id, artifact.scope)
        self._store[(artifact.id, scope_key, artifact.version)] = {
            "id": artifact.id,
            "version": artifact.version,
            "name": artifact.name,
            "artifact_type": artifact.artifact_type,
            "path": artifact.path,
            "content": artifact.content,
            "content_type": artifact.content_type,
            "object_key": artifact.object_key,
            "content_checksum": artifact.content_checksum,
            "content_size": artifact.content_size,
            "parent_version": artifact.parent_version,
            "run_id": artifact.run_id,
            "checkpoint_id": artifact.checkpoint_id,
            "visibility": artifact.visibility,
            "scope": _scope_to_json(artifact.scope),
            "source": artifact.source,
            "created_at": artifact.created_at or now,
            "updated_at": now,
        }

    async def delete_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> bool:
        exact_scope = scope or {}
        scope_key = self._key(artifact_id, scope)
        to_delete = [
            (aid, sk, v)
            for (aid, sk, v) in self._store
            if (
                aid == artifact_id
                and sk == scope_key
                and _scope_from_json(self._store[(aid, sk, v)].get("scope"))
                == exact_scope
            )
        ]
        for key in to_delete:
            del self._store[key]
        return len(to_delete) > 0

    async def get_artifact_version(
        self, artifact_id: str, version: int, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        exact_scope = scope or {}
        scope_key = self._key(artifact_id, scope)
        row = self._store.get((artifact_id, scope_key, version))
        if row is None or _scope_from_json(row.get("scope")) != exact_scope:
            return None
        return _row_to_artifact(row)

    async def list_artifact_versions(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> list[ArtifactDefinition]:
        exact_scope = scope or {}
        scope_key = self._key(artifact_id, scope)
        versions = [
            _row_to_artifact(row)
            for (aid, sk, _v), row in self._store.items()
            if (
                aid == artifact_id
                and sk == scope_key
                and _scope_from_json(row.get("scope")) == exact_scope
            )
        ]
        versions.sort(key=lambda a: a.version, reverse=True)
        return versions


class S3ArtifactStore:
    """Persist artifact manifests in a database and immutable bodies in S3.

    The wrapped store remains authoritative for identity, version, scope, and
    lifecycle metadata.  Artifact content is never sent to that store when
    this wrapper is enabled.
    """

    def __init__(
        self,
        manifest_store: ArtifactStore,
        *,
        bucket: str,
        base_prefix: str,
        hmac_key: str,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        force_path_style: bool = False,
    ) -> None:
        self._manifest_store = manifest_store
        self._bucket = bucket
        self._base_prefix = base_prefix
        self._hmac_key = hmac_key
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._force_path_style = force_path_style

    def _backend(self, scope: dict[str, str]) -> Any:
        from server.app.agent.s3_backend import S3CompatibleBackend

        return S3CompatibleBackend.from_boto3(
            bucket=self._bucket,
            prefix=S3CompatibleBackend.scope_prefix(
                base_prefix=self._base_prefix,
                effective_scope=scope,
                hmac_key=self._hmac_key,
            ),
            endpoint_url=self._endpoint_url,
            region_name=self._region_name,
            force_path_style=self._force_path_style,
        )

    @staticmethod
    def _body_metadata(content: str) -> tuple[bytes, str, int]:
        body = content.encode("utf-8")
        return body, hashlib.sha256(body).hexdigest(), len(body)

    @staticmethod
    def _path(artifact: ArtifactDefinition, checksum: str | None = None) -> str:
        digest = checksum or artifact.content_checksum
        if digest is None:
            raise RuntimeError("Artifact manifest is missing its durable-body checksum")
        return f"/artifacts/{artifact.artifact_type}/{artifact.id}/{artifact.version}/{digest}"

    @staticmethod
    def _download(backend: Any, path: str) -> bytes:
        responses = backend.download_files([path])
        if len(responses) != 1 or responses[0].error or responses[0].content is None:
            raise RuntimeError("Artifact body is unavailable from configured durable storage")
        return bytes(responses[0].content)

    async def initialize(self) -> None:
        initialize = getattr(self._manifest_store, "initialize", None)
        if initialize is not None:
            await initialize()
        try:
            self._backend({}).verify_connection()
        except Exception as exc:
            logger.warning(
                "S3 artifact storage initialization failed",
                operation="health",
                result="failure",
                error_type=type(exc).__name__,
            )
            raise
        logger.info("S3 artifact storage initialized", operation="health", result="success")

    async def close(self) -> None:
        close = getattr(self._manifest_store, "close", None)
        if close is not None:
            await close()

    async def health_check(self) -> None:
        """Verify both the authoritative manifest store and selected object store."""
        health_check = getattr(self._manifest_store, "health_check", None)
        if health_check is not None:
            await health_check()
        self._backend({}).verify_connection()

    async def _hydrate(self, artifact: ArtifactDefinition | None) -> ArtifactDefinition | None:
        if artifact is None:
            return None
        if artifact.object_key is None or artifact.content_size is None:
            raise RuntimeError("Artifact manifest is missing durable-body metadata")
        backend = self._backend(artifact.scope)
        path = self._path(artifact)
        if backend.object_key(path) != artifact.object_key:
            raise RuntimeError("Artifact manifest does not match its configured storage scope")
        body = self._download(backend, path)
        digest = hashlib.sha256(body).hexdigest()
        if digest != artifact.content_checksum or len(body) != artifact.content_size:
            logger.warning(
                "Artifact body integrity verification failed",
                artifact_type=artifact.artifact_type,
                version=artifact.version,
                operation="read",
                result="checksum_failure",
            )
            raise RuntimeError("Artifact body failed durable-storage integrity verification")
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Artifact body is not valid UTF-8") from exc
        logger.debug(
            "Artifact body loaded from S3-compatible storage",
            artifact_type=artifact.artifact_type,
            version=artifact.version,
            content_size=artifact.content_size,
            operation="read",
            result="success",
        )
        return artifact.model_copy(update={"content": content})

    async def get_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        return await self._hydrate(await self._manifest_store.get_artifact(artifact_id, scope))

    async def list_artifacts(
        self,
        scope: dict[str, str] | None = None,
        artifact_type: str | None = None,
        run_id: str | None = None,
    ) -> list[ArtifactDefinition]:
        manifests = await self._manifest_store.list_artifacts(scope, artifact_type, run_id)
        hydrated = [await self._hydrate(artifact) for artifact in manifests]
        return [artifact for artifact in hydrated if artifact is not None]

    async def upsert_artifact(self, artifact: ArtifactDefinition) -> None:
        body, checksum, content_size = self._body_metadata(artifact.content)
        backend = self._backend(artifact.scope)
        path = self._path(artifact, checksum)
        write = backend.write(path, artifact.content)
        if write.error:
            raise RuntimeError("Artifact body could not be written to configured durable storage")
        stored_body = self._download(backend, path)
        if stored_body != body or hashlib.sha256(stored_body).hexdigest() != checksum:
            logger.warning(
                "Artifact upload integrity verification failed",
                artifact_type=artifact.artifact_type,
                version=artifact.version,
                operation="write",
                result="checksum_failure",
            )
            raise RuntimeError("Artifact body failed post-upload integrity verification")
        manifest = artifact.model_copy(
            update={
                "content": "",
                "object_key": backend.object_key(path),
                "content_checksum": checksum,
                "content_size": content_size,
            }
        )
        await self._manifest_store.upsert_artifact(manifest)
        logger.info(
            "Artifact manifest activated",
            artifact_type=artifact.artifact_type,
            version=artifact.version,
            content_size=content_size,
            operation="write",
            result="success",
        )

    async def delete_artifact(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> bool:
        versions = await self._manifest_store.list_artifact_versions(artifact_id, scope)
        for artifact in versions:
            deleted = self._backend(artifact.scope).delete(self._path(artifact))
            if deleted.error:
                raise RuntimeError("Artifact body could not be deleted from configured durable storage")
        return await self._manifest_store.delete_artifact(artifact_id, scope)

    async def get_artifact_version(
        self, artifact_id: str, version: int, scope: dict[str, str] | None = None
    ) -> ArtifactDefinition | None:
        return await self._hydrate(
            await self._manifest_store.get_artifact_version(artifact_id, version, scope)
        )

    async def list_artifact_versions(
        self, artifact_id: str, scope: dict[str, str] | None = None
    ) -> list[ArtifactDefinition]:
        manifests = await self._manifest_store.list_artifact_versions(artifact_id, scope)
        hydrated = [await self._hydrate(artifact) for artifact in manifests]
        return [artifact for artifact in hydrated if artifact is not None]


__all__ = [
    "ArtifactStore",
    "MemoryArtifactStore",
    "PostgresArtifactStore",
    "S3ArtifactStore",
    "SqliteArtifactStore",
]
