"""Offline PostgreSQL inline-artifact to S3 migration, bounded and resumable.

Stop all runtime/administration writers and take a restorable database backup
before --apply. Configure the destination using normal COGNITION_S3 settings.
This maintenance command is not an online migration or a public runtime API.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from server.app.settings import Settings, get_settings
from server.app.storage.artifact_store import S3ArtifactStore, _row_to_artifact
from server.app.storage.common import effective_scope_key
from server.app.storage.factory import create_artifact_store


async def migrate_page(
    settings: Settings, *, limit: int = 100, apply: bool = False
) -> dict[str, int]:
    """Migrate one page across exact scopes, retaining failed inline rows for retry."""
    import asyncpg

    if settings.persistence_backend != "postgres" or not settings.s3_enabled:
        raise ValueError("Migration requires PostgreSQL and an explicit S3 destination")
    if not 1 <= limit <= 1000:
        raise ValueError("Page limit must be between 1 and 1000")
    dsn = settings.persistence_uri.replace("postgresql+asyncpg://", "postgresql://", 1)
    target = create_artifact_store(settings)
    if not isinstance(target, S3ArtifactStore):
        raise ValueError("Migration requires an S3 artifact store")
    connection = await asyncpg.connect(dsn)
    try:
        pending = await connection.fetchval(
            "SELECT count(*) FROM artifacts WHERE object_key IS NULL"
        )
        result = {"pending": pending, "examined": 0, "migrated": 0, "failed": 0}
        if not apply:
            return result
        await target.initialize()
        rows = await connection.fetch(
            "SELECT * FROM artifacts WHERE object_key IS NULL ORDER BY scope_key,id,version LIMIT $1",
            limit,
        )
        result["examined"] = len(rows)
        for row in rows:
            try:
                artifact = _row_to_artifact(dict(row))
                if row["scope_key"] != effective_scope_key(artifact.scope):
                    raise ValueError("Artifact scope identity is inconsistent")
                # S3ArtifactStore verifies uploaded bytes before activating the manifest.
                await target.upsert_artifact(artifact)
                saved = await target.get_artifact_version(
                    artifact.id, artifact.version, artifact.scope
                )
                if (
                    saved is None
                    or saved.content != artifact.content
                    or saved.run_id != artifact.run_id
                ):
                    raise RuntimeError("Migrated artifact verification failed")
                result["migrated"] += 1
            except Exception:
                # No content, object names, credentials or raw provider errors in output.
                result["failed"] += 1
        result["pending"] = await connection.fetchval(
            "SELECT count(*) FROM artifacts WHERE object_key IS NULL"
        )
        return result
    finally:
        try:
            await target.close()
        finally:
            await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error(
            "--apply requires --writers-stopped after stopping all writers and taking a backup"
        )
    result = asyncio.run(migrate_page(get_settings(), limit=args.limit, apply=args.apply))
    print(json.dumps(result))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
