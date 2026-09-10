"""Operator expiration affects file availability, not successful task history."""

from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import TaskState
from botocore.exceptions import ClientError
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.exceptions import ArtifactContentNotFoundError
from server.app.models import RuntimeTask, TaskStatus
from server.app.protocols.a2a.routes import _artifact_update_from_runtime_event
from server.app.protocols.a2a.task_store import CognitionTaskStore
from server.app.storage.artifact_store import MemoryArtifactStore, S3ArtifactStore
from server.app.storage.published_file import REFERENCE_PREFIX, publish_file, resolve_download
from server.app.storage.s3_object_store import S3ObjectStore


def error(code, status):
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadObject",
    )


@pytest.mark.parametrize("code", ["404", "NotFound", "NoSuchKey"])
def test_missing_object_requires_accessible_bucket(code):
    client = MagicMock()
    objects = S3ObjectStore(client, bucket="test", base_prefix="test", hmac_key="test")
    client.head_object.side_effect = error(code, 404)
    with pytest.raises(ArtifactContentNotFoundError):
        objects.require_object("test-key")
    client.head_bucket.assert_called_once_with(Bucket="test")
    client.get_object.assert_not_called()


@pytest.mark.parametrize(
    "code,status", [("403", 403), ("AccessDenied", 403), ("SlowDown", 503), ("NoSuchBucket", 404)]
)
def test_storage_failures_are_not_expiration(code, status):
    client = MagicMock()
    objects = S3ObjectStore(client, bucket="test", base_prefix="test", hmac_key="test")
    failure = error(code, status)
    client.head_object.side_effect = failure
    with pytest.raises(ClientError) as caught:
        objects.require_object("test-key")
    assert caught.value is failure
    client.head_bucket.assert_not_called()


def test_generic_404_for_deleted_bucket_is_not_expiration():
    client = MagicMock()
    objects = S3ObjectStore(client, bucket="test", base_prefix="test", hmac_key="test")
    client.head_object.side_effect = error("404", 404)
    client.head_bucket.side_effect = error("404", 404)
    with pytest.raises(ClientError):
        objects.require_object("test-key")


@pytest.fixture
def publication_store(monkeypatch):
    bodies = {}
    client = MagicMock()
    client.put_object.side_effect = lambda **kwargs: bodies.__setitem__(
        kwargs["Key"], kwargs["Body"]
    )
    client.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(bodies[kwargs["Key"]])}

    def head(**kwargs):
        if kwargs["Key"] not in bodies:
            raise error("404", 404)
        return {"ContentLength": len(bodies[kwargs["Key"]])}

    client.head_object.side_effect = head
    client.generate_presigned_url.side_effect = [
        "https://example.test/first",
        "https://example.test/refreshed",
    ]
    store = S3ArtifactStore(
        MemoryArtifactStore(), bucket="test", base_prefix="test", hmac_key="test"
    )
    objects = S3ObjectStore(client, bucket="test", base_prefix="test", hmac_key="test")
    monkeypatch.setattr(store, "_object_store", lambda: objects)
    return store, bodies, client


async def test_scoped_history_survives_expiration_and_recovers_if_restored(publication_store):
    store, bodies, client = publication_store
    scope = {"tenant": "a", "conversation": "one"}
    published = await publish_file(store, scope, b"pdf-bytes", "report.pdf", "application/pdf", 0)
    binary_put, descriptor_put = client.put_object.call_args_list
    assert binary_put.kwargs["Tagging"] == "cognition%3Acontent-class=published-file"
    assert "Tagging" not in descriptor_put.kwargs
    original = {
        "kind": "url",
        "value": REFERENCE_PREFIX + published.id,
        "media_type": "application/pdf",
        "filename": "report.pdf",
    }
    runtime = AsyncMock()
    backend = AsyncMock()
    backend.list_messages_for_session.return_value = []
    projector = CognitionTaskStore(runtime, backend, agent_name="reporter", artifact_store=store)
    task = RuntimeTask(
        id="task-one",
        context_id="conversation",
        session_id="session",
        agent_name="reporter",
        status=TaskStatus.COMPLETED,
        effective_scope=scope,
        created_at="2026-09-08T00:00:00Z",
        updated_at="2026-09-08T00:01:00Z",
        metadata={
            "artifacts": [
                {"artifact_id": published.id, "name": "report.pdf", "parts": [original]},
                {
                    "artifact_id": "summary",
                    "name": "summary",
                    "parts": [{"kind": "text", "value": "Done."}],
                },
            ]
        },
    )
    snapshot = deepcopy(task.metadata)
    first = await projector.project(task)
    assert first.artifacts[0].parts[0].url == "https://example.test/first"
    assert first.artifacts[0].parts[0].filename == "report.pdf"
    del bodies[published.object_key]
    missing = await projector.project(task)
    assert missing.status.state == TaskState.TASK_STATE_COMPLETED
    assert missing.artifacts[0].artifact_id == first.artifacts[0].artifact_id
    assert missing.artifacts[0].name == "report.pdf"
    notice = missing.artifacts[0].parts[0]
    assert notice.text == "Published content is no longer available."
    assert not notice.url and not notice.filename
    assert notice.metadata["cognition"]["contentAvailability"] == "unavailable"
    assert notice.metadata["cognition"]["originalFilename"] == "report.pdf"
    assert missing.artifacts[1] == first.artifacts[1]
    assert client.generate_presigned_url.call_count == 1
    assert task.metadata == snapshot

    resolved = await projector.resolve_part(original, scope)
    replay = _artifact_update_from_runtime_event(
        task.id,
        task.context_id,
        {**resolved, "artifact_id": published.id, "name": "report.pdf", "last_chunk": True},
    )
    assert MessageToDict(replay.artifact.parts[0]) == MessageToDict(notice)
    before = client.head_object.call_count
    with pytest.raises(ValueError):
        await projector.resolve_part(original, {"tenant": "b", "conversation": "one"})
    assert client.head_object.call_count == before

    bodies[published.object_key] = b"pdf-bytes"
    restored = await projector.project(task)
    assert restored.artifacts[0].artifact_id == published.id
    assert restored.artifacts[0].parts[0].url == "https://example.test/refreshed"
    assert task.metadata == snapshot


async def test_outage_and_bad_descriptor_fail_retrieval(publication_store):
    store, bodies, client = publication_store
    published = await publish_file(store, {}, b"body", "report.txt", "text/plain", 0)
    projector = CognitionTaskStore(
        AsyncMock(), AsyncMock(), agent_name="reporter", artifact_store=store
    )
    part = {"kind": "url", "value": REFERENCE_PREFIX + published.id}
    client.head_object.side_effect = error("AccessDenied", 403)
    with pytest.raises(ClientError):
        await projector.resolve_part(part, {})
    client.head_object.side_effect = None
    descriptor_key = client.put_object.call_args_list[1].kwargs["Key"]
    bodies[descriptor_key] = b"corrupt descriptor"
    with pytest.raises(RuntimeError, match="integrity"):
        await projector.resolve_part(part, {})
    client.generate_presigned_url.assert_not_called()


async def test_missing_file_never_gets_a_new_signature(publication_store):
    store, bodies, client = publication_store
    published = await publish_file(store, {}, b"body", "report.txt", "text/plain", 0)
    del bodies[published.object_key]
    with pytest.raises(ArtifactContentNotFoundError):
        await resolve_download(store, {}, REFERENCE_PREFIX + published.id)
    client.generate_presigned_url.assert_not_called()


async def test_tagging_permission_failure_prevents_manifest(publication_store):
    store, bodies, client = publication_store
    client.put_object.side_effect = error("AccessDenied", 403)
    with pytest.raises(ClientError):
        await publish_file(store, {}, b"body", "report.txt", "text/plain", 0)
    assert bodies == {}
    assert await store.list_artifacts({}) == []


@pytest.mark.parametrize("expire_before_stream", [False, True])
async def test_stream_and_get_task_preserve_missing_attachment(
    publication_store, setup_storage_backend, tmp_path, expire_before_stream
):
    from server.app.agent.runtime import ArtifactEvent, DoneEvent
    from tests.unit.test_a2a_jsonrpc_runtime import (
        _build_client,
        _FakeSessionAgentManager,
        _send_request,
    )

    store, bodies, s3 = publication_store
    s3.generate_presigned_url.side_effect = None
    s3.generate_presigned_url.return_value = "https://example.test/current"
    manager = _FakeSessionAgentManager(setup_storage_backend)
    publications = []

    async def execute(**kwargs):
        result = await publish_file(store, kwargs["scope"], b"file", "report.txt", "text/plain", 0)
        publications.append(result)
        if expire_before_stream:
            del bodies[result.object_key]
        yield ArtifactEvent(
            artifact_id=result.id,
            name="report.txt",
            kind="url",
            value=REFERENCE_PREFIX + result.id,
            media_type="text/plain",
            filename="report.txt",
        )
        yield DoneEvent()

    manager.service.stream_response = execute
    client = await _build_client(setup_storage_backend, tmp_path, manager, store)
    request = _send_request("retention-stream")
    request["method"] = "SendStreamingMessage"
    async with client:
        response = await client.post(
            "/a2a/researcher", json=request, headers={"Accept": "text/event-stream"}
        )
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert not any("error" in event for event in events), events
        update = next(
            event["result"]["artifactUpdate"]
            for event in events
            if "artifactUpdate" in event["result"]
        )
        part = update["artifact"]["parts"][0]
        assert update["artifact"]["artifactId"] == publications[0].id
        if expire_before_stream:
            assert part["text"] == "Published content is no longer available."
            assert part["metadata"]["cognition"]["contentAvailability"] == "unavailable"
            assert "url" not in part
        else:
            assert part["url"] == "https://example.test/current"
            del bodies[publications[0].object_key]
        fetched = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "get-retention",
                "method": "GetTask",
                "params": {"id": update["taskId"]},
            },
        )
        task = fetched.json()["result"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["artifacts"][0]["artifactId"] == publications[0].id
        notice = task["artifacts"][0]["parts"][0]
        assert notice["text"] == "Published content is no longer available."
        if expire_before_stream:
            assert notice == part
        wrong = await client.post(
            "/a2a/researcher",
            headers={"X-Cognition-Scope-Account": "other"},
            json={
                "jsonrpc": "2.0",
                "id": "wrong",
                "method": "GetTask",
                "params": {"id": update["taskId"]},
            },
        )
        assert "result" not in wrong.json()


async def test_publication_records_trusted_run_ownership(publication_store):
    store, _, _ = publication_store
    scope = {"project": "owner"}
    published = await publish_file(
        store, scope, b"body", "report.txt", "text/plain", 0, run_id="trusted-run"
    )
    descriptor = await store.get_artifact("published-" + published.id, scope)
    assert descriptor is not None
    assert descriptor.run_id == "trusted-run"
    assert descriptor.scope == scope
    assert await store.get_artifact("published-" + published.id, {"project": "sibling"}) is None


async def test_record_retention_does_not_read_or_delete_expired_s3_bytes(publication_store):
    from server.app.storage.config_models import ArtifactDefinition

    store, bodies, client = publication_store
    scope = {"project": "owner"}
    await store.upsert_artifact(ArtifactDefinition(
        id="response", name="response", content="old response", scope=scope, run_id="run"
    ))
    bodies.clear()  # Storage lifecycle has already expired the body.
    client.reset_mock()
    records = await store.list_retention_artifacts(scope, "run")
    assert len(records) == 1
    assert await store.delete_artifact_version(records[0])
    assert await store.list_retention_artifacts(scope, "run") == []
    client.get_object.assert_not_called()
    client.delete_object.assert_not_called()
    client.put_object.assert_not_called()
