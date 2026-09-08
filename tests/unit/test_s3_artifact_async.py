"""Regression coverage for responsive, bounded S3 artifact persistence."""

from __future__ import annotations

import asyncio
import io
import threading
from unittest.mock import MagicMock

import pytest

from server.app.storage.artifact_store import MemoryArtifactStore, S3ArtifactStore
from server.app.storage.config_models import ArtifactDefinition
from server.app.storage.s3_object_store import S3ObjectStore


@pytest.fixture
def storage(monkeypatch):
    bodies = {}
    client = MagicMock()
    client.put_object.side_effect = lambda **kw: bodies.__setitem__(kw["Key"], kw["Body"])
    client.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(bodies[kw["Key"]])}
    objects = S3ObjectStore(client, bucket="test", base_prefix="test", hmac_key="test")
    factory = MagicMock(return_value=objects)
    monkeypatch.setattr(S3ObjectStore, "from_boto3", factory)
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(manifests, bucket="test", base_prefix="test", hmac_key="test")
    return store, manifests, client, factory


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_slow_upload_does_not_block_event_loop_or_activate_early(storage):
    store, manifests, client, _ = storage
    entered, release = threading.Event(), threading.Event()
    original = client.put_object.side_effect

    def slow_put(**kwargs):
        entered.set()
        release.wait(2)
        original(**kwargs)

    client.put_object.side_effect = slow_put
    task = asyncio.create_task(
        store.upsert_artifact(ArtifactDefinition(id="a", name="a", content="body"))
    )
    try:
        await wait_until(entered.is_set)
        assert not task.done(), "Synchronous S3 upload blocked the event loop"
        assert await manifests.get_artifact("a") is None
    finally:
        release.set()
        await task
        await store.close()


@pytest.mark.asyncio
async def test_client_is_reused_across_concurrent_reads_writes_and_closed(storage):
    store, _, client, factory = storage
    try:
        await store.initialize()
        await asyncio.gather(
            *(
                store.upsert_artifact(ArtifactDefinition(id=str(i), name=str(i), content="body"))
                for i in range(12)
            )
        )
        results = await asyncio.gather(*(store.get_artifact(str(i)) for i in range(12)))
        assert all(r is not None and r.content == "body" for r in results)
        await store.health_check()
        assert factory.call_count == 1
    finally:
        await store.close()
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancellation_keeps_io_slots_until_workers_finish(storage):
    store, manifests, client, _ = storage
    release = threading.Event()
    entered = 0
    lock = threading.Lock()
    original = client.put_object.side_effect

    def slow_put(**kwargs):
        nonlocal entered
        with lock:
            entered += 1
        release.wait(3)
        original(**kwargs)

    client.put_object.side_effect = slow_put
    tasks = [
        asyncio.create_task(
            store.upsert_artifact(ArtifactDefinition(id=str(i), name=str(i), content="body"))
        )
        for i in range(10)
    ]
    following = None
    try:
        await wait_until(lambda: entered == 10)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        following = asyncio.create_task(
            store.upsert_artifact(ArtifactDefinition(id="next", name="next", content="body"))
        )
        await asyncio.sleep(0.05)
        assert entered == 10, "Canceled callers released slots while S3 workers were still running"
        assert await manifests.list_artifacts({}) == []
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        if following is not None:
            await following
        await store.close()
    assert entered == 11
