"""Retention protects live contexts and retries dependent-store failures."""

import os
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from server.app.agent.retention import RuntimeRetention
from server.app.models import RunStatus, SessionConfig, TaskStatus
from server.app.storage.artifact_store import MemoryArtifactStore
from server.app.storage.config_models import ArtifactDefinition
from server.app.storage.memory import MemoryStorageBackend
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture(params=["memory", "sqlite"] + (["postgres"] if os.environ.get("COGNITION_TEST_POSTGRES_DSN") else []))
async def backend(request, tmp_path):
    admin = None
    database = None
    if request.param == "postgres":
        from urllib.parse import urlsplit, urlunsplit
        from uuid import uuid4

        import asyncpg

        from server.app.storage.postgres import PostgresStorageBackend

        dsn = os.environ["COGNITION_TEST_POSTGRES_DSN"]
        parsed = urlsplit(dsn)
        if parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.path != "/cognition_retention_test":
            raise ValueError("Retention test DSN must select the disposable local database")
        admin = await asyncpg.connect(dsn)
        database = "retention_" + uuid4().hex
        await admin.execute(f'CREATE DATABASE "{database}"')
        isolated_dsn = urlunsplit(parsed._replace(path="/" + database))
        store = PostgresStorageBackend(isolated_dsn, str(tmp_path))
    else:
        store = (MemoryStorageBackend(str(tmp_path)) if request.param == "memory" else
             SqliteStorageBackend(str(tmp_path / "retention.db"), str(tmp_path)))
    await store.initialize()
    yield store
    await store.close()
    if admin is not None:
        await admin.execute(f'DROP DATABASE "{database}"')
        await admin.close()


def cutoff():
    return (datetime.now(UTC) + timedelta(days=1)).isoformat()


async def context(store, identity="a", scope=None):
    session = await store.create_session(
        identity, "thread-" + identity, SessionConfig(), "worker", scopes=scope or {"project": identity}
    )
    await store.update_session(identity, status="idle", effective_scope=session.scopes)
    return session


@pytest.mark.asyncio
async def test_dry_run_and_complete_context_cleanup(backend):
    owner = await context(backend)
    sibling = await context(backend, "b")
    await backend.create_task("task", "a", "a", "worker", owner.scopes, status=TaskStatus.COMPLETED)
    await backend.create_run("run", "a", owner.thread_id, status=RunStatus.DONE,
                             effective_scope=owner.scopes, task_id="task")
    await backend.create_message("msg", "a", "user", "private", effective_scope=owner.scopes)
    artifacts = MemoryArtifactStore()
    await artifacts.upsert_artifact(ArtifactDefinition(
        id="file", name="file", content="private", run_id="run", scope=owner.scopes
    ))
    saver = await backend.get_checkpointer()
    config = {"configurable": {"thread_id": owner.thread_id, "checkpoint_ns": ""}}
    await saver.aput(config, empty_checkpoint(), {"source": "input", "step": 0, "parents": {}}, {})
    service = RuntimeRetention(backend, artifacts)
    preview = await service.sweep(cutoff(), scope=owner.scopes)
    assert preview.eligible == 1 and preview.purged == 0
    assert await artifacts.get_artifact("file", owner.scopes) is not None
    assert await saver.aget_tuple(config) is not None
    result = await service.sweep(cutoff(), apply=True, scope=owner.scopes)
    assert result.purged == 1 and result.failed == 0
    assert await backend.get_session("a", owner.scopes) is None
    assert await backend.get_session("b", sibling.scopes) is not None
    assert await saver.aget_tuple(config) is None
    assert await artifacts.get_artifact("file", owner.scopes) is None
    if isinstance(backend, SqliteStorageBackend):
        import sqlite3
        with sqlite3.connect(backend.db_path) as db:
            for table in ("messages", "session_runs", "session_events", "runtime_tasks"):
                assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id='a'").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [RunStatus.ACTIVE, RunStatus.WAITING_FOR_APPROVAL, RunStatus.INTERRUPTED])
async def test_live_and_resumable_runs_are_protected(backend, status):
    session = await context(backend)
    await backend.create_run("run", session.id, session.thread_id, status=status,
                             effective_scope=session.scopes)
    result = await RuntimeRetention(backend, MemoryArtifactStore()).sweep(cutoff(), apply=True)
    assert result.purged == 0 and result.protected == 1
    assert await backend.get_session(session.id, session.scopes) is not None


@pytest.mark.asyncio
async def test_failed_artifact_cleanup_preserves_retry_identity_and_denies_new_work(backend, monkeypatch):
    session = await context(backend)
    await backend.create_run("run", session.id, session.thread_id, status=RunStatus.DONE,
                             effective_scope=session.scopes)
    artifacts = MemoryArtifactStore()
    await artifacts.upsert_artifact(ArtifactDefinition(
        id="file", name="file", run_id="run", scope=session.scopes
    ))
    delete = artifacts.delete_artifact_version
    monkeypatch.setattr(artifacts, "delete_artifact_version", AsyncMock(side_effect=RuntimeError("storage down")))
    service = RuntimeRetention(backend, artifacts)
    result = await service.sweep(cutoff(), apply=True)
    assert result.failed == 1 and result.purged == 0
    assert (await backend.get_session(session.id, session.scopes)).status == "expired"
    assert await backend.list_runs(session.id, session.scopes)
    with pytest.raises(ValueError, match="unavailable"):
        await backend.create_task("new", session.id, session.id, "worker", session.scopes)
    with pytest.raises(ValueError, match="unavailable"):
        await backend.create_run("new", session.id, session.thread_id, effective_scope=session.scopes)
    with pytest.raises(ValueError, match="unavailable"):
        await backend.create_message("new", session.id, "user", "new work", effective_scope=session.scopes)
    assert await backend.update_session(session.id, status="active", effective_scope=session.scopes) is None
    assert await backend.update_run("run", status=RunStatus.ACTIVE, effective_scope=session.scopes) is None
    with pytest.raises(ValueError, match="unavailable"):
        await backend.append_event("late", session.id, "run", "late", effective_scope=session.scopes)
    expired = await backend.get_session(session.id, session.scopes)
    assert await backend.rebuild_message_projection(
        session.id, session.thread_id, [], effective_scope=session.scopes
    ) == 0
    await backend.update_message_count(session.id, 99, effective_scope=session.scopes)
    unchanged = await backend.get_session(session.id, session.scopes)
    assert unchanged.updated_at == expired.updated_at
    assert unchanged.message_count == expired.message_count
    monkeypatch.setattr(artifacts, "delete_artifact_version", delete)
    result = await service.sweep(cutoff(), apply=True)
    assert result.purged == 1 and result.failed == 0


@pytest.mark.asyncio
async def test_scan_pages_are_stable_and_claim_checks_scope_and_shared_thread(backend):
    session = await context(backend)
    await context(backend, "b")
    page = await backend.scan_retention_sessions(cutoff(), limit=1)
    assert [s.id for s in page] == ["a"]
    assert [s.id for s in await backend.scan_retention_sessions(cutoff(), after_id="a", limit=1)] == ["b"]
    assert await backend.claim_retention_session(session.id, {"project": "b"}, cutoff()) is None
    await backend.create_session("shared", session.thread_id, SessionConfig(), "other", scopes={"project": "other"})
    assert await backend.claim_retention_session(session.id, session.scopes, cutoff()) is None


@pytest.mark.asyncio
async def test_new_task_and_retention_claim_serialize(backend):
    import asyncio

    session = await context(backend)
    claimed, task = await asyncio.gather(
        backend.claim_retention_session(session.id, session.scopes, cutoff()),
        backend.create_task("new", session.id, session.id, "worker", session.scopes),
        return_exceptions=True,
    )
    assert (claimed is None and not isinstance(task, Exception)) or (
        claimed is not None and isinstance(task, ValueError)
    )


def test_retention_cli_rejects_unscoped_timestamp_and_invalid_scope():
    from typer.testing import CliRunner

    from server.app.cli import app

    runner = CliRunner()
    help_result = runner.invoke(app, ["retention", "--help"])
    assert help_result.exit_code == 0
    plain_help = re.sub(r"\x1b\[[0-9;]*m", "", help_result.output)
    assert "--apply" in plain_help
    for args in (
        ["--before", "2026-01-01T00:00:00"],
        ["--before", cutoff(), "--scope-json", '["project"]'],
        ["--before", cutoff(), "--scope-json", '{"project": 12}'],
        ["--before", cutoff(), "--limit", "1001"],
    ):
        result = runner.invoke(app, ["retention", *args])
        assert result.exit_code == 2


@pytest.mark.asyncio
async def test_retention_preserves_version_created_after_ownership_check(backend, monkeypatch):
    """Another context can publish after inspection but before artifact deletion."""
    owner = await context(backend)
    sibling = await context(backend, "b", scope=owner.scopes)
    await backend.create_run("old-run", owner.id, owner.thread_id,
                             status=RunStatus.DONE, effective_scope=owner.scopes)
    await backend.create_run("live-run", sibling.id, sibling.thread_id,
                             status=RunStatus.ACTIVE, effective_scope=sibling.scopes)
    artifacts = MemoryArtifactStore()
    await artifacts.upsert_artifact(ArtifactDefinition(
        id="shared", name="shared", version=1, content="old result",
        run_id="old-run", scope=owner.scopes,
    ))
    original_delete = artifacts.delete_artifact_version

    async def concurrent_publish_then_delete(expected):
        await artifacts.upsert_artifact(ArtifactDefinition(
            id="shared", name="shared", version=2, content="live result",
            run_id="live-run", scope=owner.scopes,
        ))
        return await original_delete(expected)

    monkeypatch.setattr(artifacts, "delete_artifact_version", concurrent_publish_then_delete)
    await RuntimeRetention(backend, artifacts).sweep(cutoff(), apply=True)
    live = await artifacts.get_artifact_version("shared", 2, owner.scopes)
    assert live is not None, "Retention deleted a version owned by a live sibling context"
    assert live.content == "live result"
    assert await backend.get_session(sibling.id, sibling.scopes) is not None


@pytest.mark.asyncio
async def test_conditional_artifact_delete_rejects_replacement_and_preserves_new_version(backend):
    from server.app.storage.artifact_store import PostgresArtifactStore, SqliteArtifactStore
    from server.app.storage.postgres import PostgresStorageBackend

    if isinstance(backend, PostgresStorageBackend):
        artifacts = PostgresArtifactStore(backend.connection_string)
    elif isinstance(backend, SqliteStorageBackend):
        artifacts = SqliteArtifactStore(backend.db_path)
    else:
        artifacts = MemoryArtifactStore()
    await artifacts.initialize()
    try:
        original = ArtifactDefinition(id="result", name="result", version=1,
                                      content="old", run_id="old-run", scope={"project": "a"})
        await artifacts.upsert_artifact(original)
        observed = await artifacts.get_artifact_version(original.id, 1, original.scope)
        await artifacts.upsert_artifact(original.model_copy(update={"run_id": "live-run"}))
        assert not await artifacts.delete_artifact_version(observed)
        replacement = await artifacts.get_artifact_version(original.id, 1, original.scope)
        assert replacement.run_id == "live-run"
        await artifacts.upsert_artifact(original.model_copy(update={"version": 2}))
        assert await artifacts.delete_artifact_version(replacement)
        assert await artifacts.get_artifact_version(original.id, 2, original.scope) is not None
        assert not await artifacts.delete_artifact_version(replacement)

        # Independent database operations contend on the same row. Whichever
        # wins, cleanup must never remove the replacement's committed value.
        import asyncio

        contested = original.model_copy(update={"version": 3})
        await artifacts.upsert_artifact(contested)
        snapshot = await artifacts.get_artifact_version(original.id, 3, original.scope)
        await asyncio.gather(
            artifacts.upsert_artifact(contested.model_copy(update={"run_id": "live-run", "content": "new"})),
            artifacts.delete_artifact_version(snapshot),
        )
        survivor = await artifacts.get_artifact_version(original.id, 3, original.scope)
        assert survivor is not None and survivor.run_id == "live-run"
        assert survivor.content == "new"
        assert not await artifacts.delete_artifact_version(
            survivor.model_copy(update={"scope": {"project": "sibling"}})
        )
        assert await artifacts.get_artifact_version(original.id, 3, original.scope) == survivor
    finally:
        await artifacts.close()


def test_session_retention_defaults_off_and_cli_requires_enablement(monkeypatch):
    from typer.testing import CliRunner

    import server.app.cli as cli
    from server.app.settings import Settings

    settings = Settings(_env_file=None, COGNITION_SESSION_RETENTION_ENABLED=False,
                        COGNITION_SESSION_RETENTION_DAYS=14,
                        COGNITION_SESSION_RETENTION_BATCH_SIZE=25,
                        COGNITION_SESSION_RETENTION_INTERVAL_SECONDS=60)
    assert settings.session_retention_days == 14
    assert settings.session_retention_batch_size == 25
    assert settings.session_retention_interval_seconds == 60
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["retention", "--apply"])
    assert result.exit_code == 2
    assert "COGNITION_SESSION_RETENTION_ENABLED" in result.output


@pytest.mark.asyncio
async def test_worker_pages_retries_and_observes_interval(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from server.app.agent.retention import RetentionResult, watch_retention

    service = AsyncMock()
    service.sweep.side_effect = [
        RetentionResult(scanned=2, next_cursor="page-2"),
        RuntimeError("database unavailable"),
        RetentionResult(scanned=1),
        RetentionResult(scanned=0),
    ]
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    worker = watch_retention(service, days=14, limit=2, interval_seconds=17)
    try:
        results = [await anext(worker) for _ in range(4)]
    finally:
        await worker.aclose()
    assert results[1].failed == 1
    assert [call.kwargs["after_id"] for call in service.sweep.call_args_list] == ["", "page-2", "page-2", ""]
    assert all(call.kwargs["apply"] for call in service.sweep.call_args_list)
    assert sleep.await_count == 3
    sleep.assert_awaited_with(17)


def test_record_only_factory_does_not_require_s3_configuration(tmp_path):
    from types import SimpleNamespace

    from server.app.storage.factory import create_artifact_store

    settings = SimpleNamespace(persistence_backend="memory", workspace_path=tmp_path, s3_enabled=True)
    store = create_artifact_store(settings, include_durable_bodies=False)
    assert isinstance(store, MemoryArtifactStore)


@pytest.mark.parametrize("enabled,task_ttl,valid", [(False, 60, True), (True, 0, True), (True, 60, False)])
def test_session_retention_cannot_overlap_legacy_task_cleanup(enabled, task_ttl, valid):
    from pydantic import ValidationError

    from server.app.settings import Settings

    options = {
        "_env_file": None,
        "COGNITION_SESSION_RETENTION_ENABLED": enabled,
        "COGNITION_A2A_TERMINAL_TASK_TTL_SECONDS": task_ttl,
    }
    if valid:
        assert Settings(**options).session_retention_enabled == enabled
    else:
        with pytest.raises(ValidationError, match="TERMINAL_TASK_TTL_SECONDS=0"):
            Settings(**options)


def test_retention_cli_uses_configured_age_and_leaves_fresh_and_live_contexts(tmp_path, monkeypatch):
    import asyncio
    import json
    import sqlite3

    from typer.testing import CliRunner

    import server.app.cli as cli
    from server.app.settings import Settings
    from server.app.storage.artifact_store import SqliteArtifactStore

    database = tmp_path / "cli-retention.db"
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()

    async def seed():
        store = SqliteStorageBackend(str(database), str(tmp_path))
        await store.initialize()
        artifacts = SqliteArtifactStore(str(database))
        await artifacts.initialize()
        try:
            for identity in ("old", "fresh", "live"):
                session = await context(store, identity)
                await store.create_run(identity + "-run", identity, session.thread_id,
                    status=RunStatus.ACTIVE if identity == "live" else RunStatus.DONE,
                    effective_scope=session.scopes)
            await store.create_message("old-msg", "old", "assistant", "history",
                                       effective_scope={"project": "old"})
            await artifacts.upsert_artifact(ArtifactDefinition(
                id="old-result", name="response", content="", scope={"project": "old"},
                run_id="old-run", object_key="expired/body", content_checksum="0" * 64,
                content_size=7,
            ))
        finally:
            await artifacts.close()
            await store.close()

    asyncio.run(seed())
    with sqlite3.connect(database) as db:
        db.execute("UPDATE sessions SET updated_at=? WHERE id IN ('old','live')", (old,))
        db.execute("UPDATE session_runs SET updated_at=? WHERE session_id IN ('old','live')", (old,))
        db.execute("UPDATE messages SET created_at=? WHERE session_id='old'", (old,))
    settings = Settings(_env_file=None, COGNITION_SESSION_RETENTION_ENABLED=True,
        COGNITION_SESSION_RETENTION_DAYS=30, COGNITION_A2A_TERMINAL_TASK_TTL_SECONDS=0,
        COGNITION_PERSISTENCE_BACKEND="sqlite", COGNITION_PERSISTENCE_URI=str(database),
        COGNITION_LOCAL_WORKSPACE_ROOT=tmp_path, COGNITION_DURABLE_FILE_BACKEND="s3")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    runner = CliRunner()
    preview = runner.invoke(cli.app, ["retention"])
    assert preview.exit_code == 0, preview.output
    assert json.loads("{" + preview.output.rsplit("{", 1)[1])["purged"] == 0
    applied = runner.invoke(cli.app, ["retention", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert json.loads("{" + applied.output.rsplit("{", 1)[1])["purged"] == 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM sessions ORDER BY id").fetchall() == [("fresh",), ("live",)]
        assert db.execute("SELECT count(*) FROM messages WHERE session_id='old'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM artifacts WHERE id='old-result'").fetchone() == (0,)
