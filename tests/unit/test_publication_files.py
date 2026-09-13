"""Binary publication and sandbox file boundary regression tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.storage.artifact_store import MemoryArtifactStore, S3ArtifactStore
from server.app.storage.published_file import REFERENCE_PREFIX, publish_file, resolve_download


def runtime_module():
    path = (
        Path(__file__).parents[2] / "examples/aws-lambda-microvm-default-runtime/runtime/server.py"
    )
    spec = importlib.util.spec_from_file_location("publication_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "nested/../file", "link"])
def test_publication_rejects_unsafe_paths(tmp_path, path):
    module = runtime_module()
    module.WORKSPACE_ROOT = tmp_path
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises((ValueError, OSError)):
        module.read_publication_file(path, 1024)


def test_publication_preserves_binary_and_limits_reads(tmp_path):
    module = runtime_module()
    module.WORKSPACE_ROOT = tmp_path
    body = b"\x00\xffbinary"
    (tmp_path / "file").write_bytes(body)
    assert module.read_publication_file("file", len(body)) == body
    with pytest.raises(ValueError):
        module.read_publication_file("file", len(body) - 1)


@pytest.mark.asyncio
async def test_binary_snapshot_and_scoped_url(monkeypatch):
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(manifests, bucket="test", base_prefix="test", hmac_key="test")
    objects = MagicMock()
    bodies = {}
    objects.scoped_key.side_effect = lambda scope, path: str(scope) + path
    objects.put.side_effect = lambda key, body, **kwargs: bodies.__setitem__(key, body)
    objects.get.side_effect = lambda key: bodies[key]
    objects.download_url.return_value = "https://example.test/signed"
    monkeypatch.setattr(store, "_object_store", lambda: objects)
    result = await publish_file(
        store, {"tenant": "a"}, b"\xff\x00", "file.bin", "application/octet-stream", 1
    )
    assert result.kind == "url"
    assert bodies[result.object_key] == b"\xff\x00"
    assert (
        await resolve_download(store, {"tenant": "a"}, REFERENCE_PREFIX + result.id)
        == "https://example.test/signed"
    )
    with pytest.raises(ValueError):
        await resolve_download(store, {"tenant": "b"}, REFERENCE_PREFIX + result.id)
    assert objects.download_url.call_count == 1


@pytest.mark.asyncio
async def test_failed_upload_never_activates_manifest(monkeypatch):
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(manifests, bucket="test", base_prefix="test", hmac_key="test")
    objects = MagicMock()
    objects.put.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr(store, "_object_store", lambda: objects)
    with pytest.raises(RuntimeError):
        await publish_file(store, {}, b"body", "file.bin", "application/octet-stream", 1)
    assert await manifests.list_artifacts({}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "upload", "manifest", "oversize", "scope", "traversal", "sibling", "disabled", "revoked_during_read", "limit_reduced", "policy_unavailable"])
@pytest.mark.parametrize("inline_limit", [0, 256 * 1024])
@pytest.mark.parametrize("workspace_root", ["/workspace", "/work"])
async def test_real_tool_graph_emits_published_part(monkeypatch, failure, inline_limit, workspace_root):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from server.app.agent.cognition_agent import CognitionContext
    from server.app.agent.middleware import ToolArgumentValidationMiddleware
    from server.app.agent.publication import PublicationMiddleware
    from server.app.agent.runtime import ArtifactEvent, DeepAgentRuntime

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.messages import AIMessageChunk
            from langchain_core.outputs import ChatGenerationChunk

            message = next(self.messages)
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls)
            )

    store = S3ArtifactStore(
        MemoryArtifactStore(), bucket="test", base_prefix="test", hmac_key="test"
    )
    objects = MagicMock()
    bodies = {}
    objects.scoped_key.side_effect = lambda scope, path: str(scope) + path
    objects.put.side_effect = lambda key, body, **kwargs: bodies.__setitem__(key, body)
    objects.get.side_effect = lambda key: bodies[key]
    monkeypatch.setattr(store, "_object_store", lambda: objects)
    sandbox = MagicMock()
    sandbox.workspace_root = workspace_root
    sandbox.download_file_bounded.return_value = b"\x00\xff"
    if failure == "oversize":
        sandbox.download_file_bounded.side_effect = ValueError("File exceeds publication limit")
    elif failure == "upload":
        objects.put.side_effect = RuntimeError("Upload unavailable")
    elif failure == "manifest":
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            store, "upsert_artifact", AsyncMock(side_effect=RuntimeError("Manifest unavailable"))
        )
    model = Model(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "publish_artifact",
                            "args": {
                                "path": (
                                    f"{workspace_root}/../secret" if failure == "traversal"
                                    else f"{workspace_root}-other/file.bin" if failure == "sibling"
                                    else f"{workspace_root}/file.bin"
                                )
                            },
                            "id": "publish-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Processed the tool result."),
            ]
        )
    )
    checkpointer = InMemorySaver()
    from unittest.mock import AsyncMock

    from server.app.agent.publication_policy import PublicationLimits

    resolver = AsyncMock(return_value=PublicationLimits(True, 10 * 1024 * 1024, inline_limit))
    if failure == "disabled":
        resolver.return_value = PublicationLimits(False, 10 * 1024 * 1024, inline_limit)
    elif failure == "revoked_during_read":
        resolver.side_effect = [
            PublicationLimits(True, 10 * 1024 * 1024, inline_limit),
            PublicationLimits(False, 10 * 1024 * 1024, inline_limit),
        ]
    elif failure == "limit_reduced":
        resolver.side_effect = [
            PublicationLimits(True, 10 * 1024 * 1024, inline_limit),
            PublicationLimits(True, 1, 0),
        ]
    elif failure == "policy_unavailable":
        resolver.side_effect = RuntimeError("Policy unavailable")
    graph = create_agent(
        model=model,
        middleware=[
            PublicationMiddleware(sandbox, store, {"tenant": "a"}, inline_limit=inline_limit,
                                  policy_resolver=resolver),
            ToolArgumentValidationMiddleware(),
        ],
        context_schema=CognitionContext,
        checkpointer=checkpointer,
    )
    runtime = DeepAgentRuntime(
        graph,
        checkpointer,
        thread_id="publication",
        context=CognitionContext(
            effective_scope={"tenant": "other" if failure == "scope" else "a"}
        ),
    )
    events = [event async for event in runtime.astream_events("Publish the file")]
    artifacts = [event for event in events if isinstance(event, ArtifactEvent)]
    if failure:
        assert artifacts == []
        if failure in {"scope", "traversal", "sibling", "disabled", "policy_unavailable"}:
            sandbox.download_file_bounded.assert_not_called()
        if failure in {"disabled", "policy_unavailable", "revoked_during_read", "limit_reduced"}:
            objects.put.assert_not_called()
        return
    assert len(artifacts) == 1, events
    if inline_limit:
        assert artifacts[0].kind == "raw"
        assert artifacts[0].value == b"\x00\xff"
    else:
        assert artifacts[0].kind == "url"
        assert artifacts[0].value.startswith(REFERENCE_PREFIX)
    binary_write, descriptor_write = objects.put.call_args_list
    assert binary_write.kwargs == {"tags": {"cognition:content-class": "published-file"}}
    assert descriptor_write.kwargs == {"tags": {"cognition:content-class": "publication-descriptor"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["checksum", "manifest"])
async def test_failed_verification_or_manifest_does_not_publish(monkeypatch, failure):
    from unittest.mock import AsyncMock

    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(manifests, bucket="test", base_prefix="test", hmac_key="test")
    objects = MagicMock()
    objects.scoped_key.return_value = "test-object"
    objects.get.return_value = b"corrupt" if failure == "checksum" else b"body"
    monkeypatch.setattr(store, "_object_store", lambda: objects)
    if failure == "manifest":
        monkeypatch.setattr(
            manifests,
            "upsert_artifact",
            AsyncMock(side_effect=RuntimeError("manifest unavailable")),
        )
    with pytest.raises(RuntimeError):
        await publish_file(store, {}, b"body", "file.bin", "application/octet-stream", 1)
    assert await manifests.list_artifacts({}) == []


def test_download_transport_rejects_oversized_body_before_json_decode():
    import httpx

    from langchain_aws_lambda_microvms.sandbox import LambdaMicroVmSandbox

    backend = object.__new__(LambdaMicroVmSandbox)
    backend._auth_headers = {"Authorization": "test"}
    backend._runtime_url = lambda path: "https://runtime.test" + path
    backend._http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 100000))
    )
    with pytest.raises(ValueError, match="exceeds limit"):
        backend._runtime_request("POST", "/download", max_response_bytes=100)
    backend._http_client.close()


def test_publication_rejects_symlink_workspace_root(tmp_path):
    module = runtime_module()
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "file").write_bytes(b"body")
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    module.WORKSPACE_ROOT = linked
    with pytest.raises(OSError):
        module.read_publication_file("file", 100)


@pytest.mark.asyncio
async def test_concurrent_tenants_and_forged_snapshot_references(monkeypatch):
    import asyncio

    from server.app.storage.config_models import ArtifactDefinition

    store = S3ArtifactStore(
        MemoryArtifactStore(), bucket="test", base_prefix="test", hmac_key="test"
    )
    objects = MagicMock()
    bodies = {}
    objects.scoped_key.side_effect = lambda scope, path: str(sorted(scope.items())) + path
    objects.put.side_effect = lambda key, body, **kwargs: bodies.__setitem__(key, body)
    objects.get.side_effect = lambda key: bodies[key]
    objects.download_url.return_value = "https://example.test/signed"
    monkeypatch.setattr(store, "_object_store", lambda: objects)
    scopes = [{"tenant": "a", "project": "shared"}, {"tenant": "b", "project": "shared"}]
    first, second = await asyncio.gather(
        *[
            publish_file(store, scope, body, "report.bin", "application/octet-stream", 0)
            for scope, body in zip(scopes, [b"tenant-a", b"tenant-b"], strict=True)
        ]
    )
    assert first.id != second.id and first.object_key != second.object_key
    assert bodies[first.object_key] == b"tenant-a"
    assert bodies[second.object_key] == b"tenant-b"
    for wrong_scope in [scopes[1], {"tenant": "a"}, {**scopes[0], "user": "extra"}]:
        with pytest.raises(ValueError):
            await resolve_download(store, wrong_scope, REFERENCE_PREFIX + first.id)
    forged = first.model_copy(update={"object_key": second.object_key})
    await store.upsert_artifact(
        ArtifactDefinition(
            id="published-" + first.id,
            name="published-" + first.id,
            artifact_type="file",
            path="published/" + first.id,
            scope=scopes[0],
            source="api",
            content=forged.model_dump_json(),
            content_type="application/vnd.cognition.published-file+json",
        )
    )
    with pytest.raises(ValueError, match="scope or identity"):
        await resolve_download(store, scopes[0], REFERENCE_PREFIX + first.id)
    objects.download_url.assert_not_called()
    objects.require_object.assert_not_called()
