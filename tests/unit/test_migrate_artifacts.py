"""Offline migration never activates unchecked bytes or loses scoped identities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.app.storage import migrate_artifacts
from server.app.storage.artifact_store import S3ArtifactStore
from server.app.storage.common import effective_scope_key


@pytest.fixture
def migration(monkeypatch):
    connection = AsyncMock()
    connection.fetchval.return_value = 2
    target = MagicMock(spec=S3ArtifactStore)
    target.initialize = AsyncMock()
    target.close = AsyncMock()
    target.upsert_artifact = AsyncMock()
    target.get_artifact_version = AsyncMock()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr("asyncpg.connect", connect)
    monkeypatch.setattr(migrate_artifacts, "create_artifact_store", lambda _: target)
    settings = SimpleNamespace(
        persistence_backend="postgres", s3_enabled=True, persistence_uri="postgresql://fixture"
    )
    return settings, connection, target, connect


@pytest.mark.asyncio
async def test_preview_does_not_initialize_or_write_storage(migration):
    settings, connection, target, _ = migration
    result = await migrate_artifacts.migrate_page(settings)
    assert result == {"pending": 2, "examined": 0, "migrated": 0, "failed": 0}
    target.initialize.assert_not_awaited()
    target.upsert_artifact.assert_not_awaited()
    connection.fetch.assert_not_awaited()
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_upload_retains_retry_and_sibling_scope(migration):
    settings, connection, target, _ = migration
    connection.fetch.return_value = [
        {
            "id": "same",
            "version": 2,
            "name": "output",
            "artifact_type": "file",
            "content": "first",
            "scope": {"project": "one"},
            "scope_key": effective_scope_key({"project": "one"}),
            "run_id": "run-one",
        },
        {
            "id": "same",
            "version": 2,
            "name": "output",
            "artifact_type": "file",
            "content": "second",
            "scope": {"project": "two"},
            "scope_key": effective_scope_key({"project": "two"}),
            "run_id": "run-two",
        },
    ]
    target.upsert_artifact.side_effect = [RuntimeError("provider private details"), None]
    target.get_artifact_version.side_effect = lambda *_: target.upsert_artifact.call_args.args[0]
    connection.fetchval.side_effect = [2, 1]
    result = await migrate_artifacts.migrate_page(settings, limit=2, apply=True)
    assert result == {"pending": 1, "examined": 2, "migrated": 1, "failed": 1}
    assert connection.fetch.call_args.args[1] == 2
    first, second = [call.args[0] for call in target.upsert_artifact.call_args_list]
    assert (first.scope, first.content, first.run_id) == ({"project": "one"}, "first", "run-one")
    assert (second.scope, second.content, second.run_id) == (
        {"project": "two"},
        "second",
        "run-two",
    )
    target.get_artifact_version.assert_awaited_once_with("same", 2, {"project": "two"})
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_closes_when_storage_close_fails(migration):
    settings, connection, target, _ = migration
    target.close.side_effect = RuntimeError("close failed")
    with pytest.raises(RuntimeError, match="close failed"):
        await migrate_artifacts.migrate_page(settings)
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 1001])
async def test_invalid_page_never_connects(migration, limit):
    settings, _, _, connect = migration
    with pytest.raises(ValueError, match="Page limit"):
        await migrate_artifacts.migrate_page(settings, limit=limit)
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_scope_is_not_reinterpreted_as_shared(migration):
    settings, connection, target, _ = migration
    connection.fetch.return_value = [
        {
            "id": "private",
            "version": 1,
            "name": "output",
            "artifact_type": "file",
            "content": "private",
            "scope": "invalid-json",
            "scope_key": effective_scope_key({"project": "private"}),
        }
    ]
    result = await migrate_artifacts.migrate_page(settings, apply=True)
    assert result["failed"] == 1
    target.upsert_artifact.assert_not_awaited()
