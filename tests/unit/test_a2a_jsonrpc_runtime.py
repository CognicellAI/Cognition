"""End-to-end unit tests for the strict A2A 1.0 JSON-RPC facade."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.agent.runtime import (
    ArtifactEvent,
    DirectMessageEvent,
    DoneEvent,
    ErrorEvent,
    InterruptEvent,
    TokenEvent,
)
from server.app.llm.deep_agent_service import SessionAgentManager
from server.app.protocols.a2a.a2ui import A2UI_EXTENSION_URI, A2UI_MEDIA_TYPE, BASIC_CATALOG_ID
from server.app.protocols.a2a.routes import _artifact_update_from_runtime_event, mount_a2a_routes
from server.app.settings import Settings
from server.app.storage.artifact_store import MemoryArtifactStore
from server.app.storage.backend import StorageBackend
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


class _FakeAgentService:
    def __init__(self, store: StorageBackend) -> None:
        self._store = store
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[dict] = []

    async def stream_response(self, **kwargs):
        self.calls.append(kwargs)
        messages = await self._store.list_messages_for_session(
            kwargs["session_id"],
            kwargs["scope"],
        )
        message_id = messages[-1].id
        if message_id.startswith("slow-message"):
            self.started.set()
            await self.release.wait()
        if message_id.startswith("slow-a2ui-output"):
            self.started.set()
            await self.release.wait()
        if message_id.startswith("tck-message-response"):
            yield DirectMessageEvent(content="Direct message response")
            return
        if message_id.startswith("tck-input-required"):
            yield InterruptEvent(
                tool_call_id="approval-1",
                tool_name="publish",
                args={},
            )
            return
        if message_id.startswith("execution-timeout"):
            yield ErrorEvent(
                message="Agent execution exceeded the configured deadline",
                code="EXECUTION_TIMEOUT",
            )
            return
        data_values: dict[str, object] = {
            "object": {"key": "value", "count": 42},
            "array": ["proposal", 2],
            "string": "proposal",
            "number": 7,
            "boolean": True,
            "null": None,
        }
        data_variant = next(
            (
                name
                for name in data_values
                if message_id.startswith(f"tck-artifact-data-{name}")
            ),
            None,
        )
        if data_variant is not None:
            yield ArtifactEvent(
                artifact_id="data-output",
                name="response",
                kind="data",
                value=data_values[data_variant],
                media_type="application/json",
            )
            yield DoneEvent()
            return
        if message_id.startswith("tck-artifact-file-url"):
            yield ArtifactEvent(
                artifact_id="url-output",
                name="response",
                kind="url",
                value="https://example.com/output.txt",
                filename="output.txt",
                media_type="text/plain",
            )
            yield DoneEvent()
            return
        if message_id.startswith("tck-artifact-file"):
            yield ArtifactEvent(
                artifact_id="file-output",
                name="response",
                kind="raw",
                value=b"file output",
                filename="output.txt",
                media_type="text/plain",
            )
            yield DoneEvent()
            return
        if message_id.startswith("chunked-output"):
            for token in ("one ", "two ", "three ", "four ", "five"):
                yield TokenEvent(content=token)
            yield DoneEvent()
            return
        if message_id.startswith("a2ui-output") or message_id.startswith("slow-a2ui-output"):
            yield TokenEvent(content="Here is a surface.")
            yield ArtifactEvent(
                artifact_id="a2ui-output",
                name="a2ui",
                kind="data",
                value=[
                    {
                        "version": "v1.0",
                        "createSurface": {
                            "surfaceId": "main",
                            "catalogId": BASIC_CATALOG_ID,
                        },
                    },
                    {
                        "version": "v1.0",
                        "updateComponents": {
                            "surfaceId": "main",
                            "components": [
                                {
                                    "id": "root",
                                    "component": "Text",
                                    "text": "Hello from A2UI",
                                }
                            ],
                        },
                    },
                ],
                media_type=A2UI_MEDIA_TYPE,
                extensions=(A2UI_EXTENSION_URI,),
            )
            yield DoneEvent()
            return
        yield TokenEvent(content="A2A works")
        yield DoneEvent()


class _FakeSessionAgentManager:
    def __init__(self, store: StorageBackend) -> None:
        self.service = _FakeAgentService(store)

    def get_service(self, _session_id: str):
        return self.service

    def register_session(self, _session_id: str, _workspace_path: str):
        return self.service

    async def abort_session(self, _session_id: str, _thread_id: str) -> bool:
        return True


async def _build_client(
    store: StorageBackend,
    tmp_path,
    manager: _FakeSessionAgentManager | None = None,
    artifact_store: MemoryArtifactStore | None = None,
    max_raw_part_bytes: int = 10 * 1024 * 1024,
    stream_chunk_bytes: int = 4096,
    input_modes: list[str] | None = None,
    a2ui_enabled: bool = False,
) -> httpx.AsyncClient:
    app = FastAPI()
    config_store = DefaultConfigStore(MemoryConfigRegistry())
    await config_store.upsert_agent(
        "researcher",
        {"account": "acme"},
        {
            "name": "researcher",
            "system_prompt": "Research carefully",
            "mode": "primary",
            "a2a": {
                "exposed": True,
                "default_input_modes": input_modes
                or ["text/plain", "application/json"],
                **({"a2ui": {"version": "1.0", "catalogs": ["basic"]}} if a2ui_enabled else {}),
            },
        },
    )
    artifact_store = artifact_store or MemoryArtifactStore()

    class _Settings:
        scope_keys = ["account"]
        scoping_enabled = True
        workspace_path = tmp_path
        a2a_max_raw_part_bytes = max_raw_part_bytes
        a2a_stream_chunk_bytes = stream_chunk_bytes

    await mount_a2a_routes(
        app,
        settings=cast(Settings, _Settings()),
        config_store=config_store,
        session_agent_manager=cast(
            SessionAgentManager,
            manager or _FakeSessionAgentManager(store),
        ),
        store=store,
        version="0.10.0",
        artifact_store=artifact_store,
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={
            "A2A-Version": "1.0",
            "X-Cognition-Scope-Account": "acme",
        },
    )


async def test_duplicate_in_flight_message_recovers_same_working_task(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(setup_storage_backend, tmp_path, manager)

    async with client:
        first = asyncio.create_task(
            client.post("/a2a/researcher", json=_send_request("slow-message-1"))
        )
        await asyncio.wait_for(manager.service.started.wait(), timeout=5)

        duplicate = await client.post(
            "/a2a/researcher",
            json=_send_request("slow-message-1"),
        )
        duplicate_task = duplicate.json()["result"]["task"]
        assert duplicate_task["status"]["state"] == "TASK_STATE_WORKING"

        manager.service.release.set()
        completed_task = (await first).json()["result"]["task"]
        assert completed_task["id"] == duplicate_task["id"]
        assert completed_task["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_subscribe_replay_ends_with_terminal_task_after_artifact_updates(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    """A replayed artifact update must not follow the terminal task event."""
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(setup_storage_backend, tmp_path, manager)
    request = _send_request("slow-message-subscribe-order")

    async def collect_subscription(task_id: str) -> list[dict]:
        events: list[dict] = []
        async with client.stream(
            "POST",
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "subscribe-order",
                "method": "SubscribeToTask",
                "params": {"id": task_id},
            },
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return events

    async with client:
        execution = asyncio.create_task(client.post("/a2a/researcher", json=request))
        await asyncio.wait_for(manager.service.started.wait(), timeout=5)
        duplicate = await client.post("/a2a/researcher", json=request)
        task_id = duplicate.json()["result"]["task"]["id"]
        subscription = asyncio.create_task(collect_subscription(task_id))
        await asyncio.sleep(0.05)
        manager.service.release.set()
        await execution
        events = await subscription

    results = [event["result"] for event in events]
    assert any("artifactUpdate" in result for result in results)
    assert results[-1]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def _send_request(message_id: str = "message-1") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "rpc-1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": message_id,
                "role": "ROLE_USER",
                "parts": [{"text": "Analyze", "mediaType": "text/plain"}],
            }
        },
    }


def _a2ui_request(message_id: str = "a2ui-output-1") -> dict:
    request = _send_request(message_id)
    request["params"]["message"]["metadata"] = {
        "a2uiRendererCapabilities": {
            "v1.0": {
                "supportedCatalogIds": [BASIC_CATALOG_ID],
            }
        }
    }
    return request


def _artifact_with_part(task: dict, part_key: str) -> dict:
    return next(
        artifact
        for artifact in task["artifacts"]
        if artifact.get("parts") and part_key in artifact["parts"][0]
    )


def _part_value(task: dict, part_key: str) -> object:
    return _artifact_with_part(task, part_key)["parts"][0][part_key]


def _sse_events_from_text(text: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


async def test_send_get_list_and_idempotency_use_durable_runtime(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        sent = await client.post("/a2a/researcher", json=_send_request())
        repeated = await client.post("/a2a/researcher", json=_send_request())

        assert sent.status_code == 200
        body = sent.json()
        task = body["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["history"][0]["role"] == "ROLE_USER"
        assert task["artifacts"][0]["artifactId"].endswith("-response")
        assert repeated.json()["result"]["task"]["id"] == task["id"]

        fetched = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-2",
                "method": "GetTask",
                "params": {"id": task["id"]},
            },
        )
        assert fetched.json()["result"]["id"] == task["id"]

        listed = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-3",
                "method": "ListTasks",
                "params": {"pageSize": 10, "includeArtifacts": True},
            },
        )
        assert [item["id"] for item in listed.json()["result"]["tasks"]] == [task["id"]]


async def test_a2ui_disabled_agent_card_is_unchanged(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)

    async with client:
        response = await client.get("/a2a/researcher/.well-known/agent-card.json")

    card = response.json()
    assert "extensions" not in card["capabilities"]
    assert A2UI_MEDIA_TYPE not in card["defaultInputModes"]
    assert A2UI_MEDIA_TYPE not in card["defaultOutputModes"]


async def test_a2ui_enabled_agent_card_advertises_optional_basic_catalog(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path, a2ui_enabled=True)

    async with client:
        response = await client.get("/a2a/researcher/.well-known/agent-card.json")

    card = response.json()
    extension = card["capabilities"]["extensions"][0]
    assert extension["uri"] == "https://a2ui.org/a2a-extension/a2ui/v1.0"
    assert extension.get("required", False) is False
    assert extension["params"] == {
        "acceptsInlineCatalogs": False,
        "supportedCatalogIds": [BASIC_CATALOG_ID],
    }
    assert A2UI_MEDIA_TYPE in card["defaultInputModes"]
    assert A2UI_MEDIA_TYPE in card["defaultOutputModes"]


async def test_a2ui_negotiated_request_returns_text_and_canonical_data_part(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path, a2ui_enabled=True)
    request = _a2ui_request("a2ui-output-1")

    async with client:
        response = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": "https://a2ui.org/a2a-extension/a2ui/v1.0"},
        )
        fetched = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-a2ui-fetch",
                "method": "GetTask",
                "params": {"id": response.json()["result"]["task"]["id"]},
            },
        )

    assert response.headers["A2A-Extensions"] == (
        "https://a2ui.org/a2a-extension/a2ui/v1.0"
    )
    task = response.json()["result"]["task"]
    artifacts = task["artifacts"]
    parts = [artifact["parts"][0] for artifact in artifacts]
    text_part = next(part for part in parts if "text" in part)
    data_artifact = _artifact_with_part(task, "data")
    data_part = data_artifact["parts"][0]
    assert text_part["text"] == "Here is a surface."
    assert data_artifact["extensions"] == [A2UI_EXTENSION_URI]
    assert data_part["mediaType"] == A2UI_MEDIA_TYPE
    assert isinstance(data_part["data"], list)
    assert data_part["data"][0]["version"] == "v1.0"
    assert "kind" not in data_part

    fetched_artifacts = fetched.json()["result"]["artifacts"]
    fetched_data_artifact = next(
        artifact for artifact in fetched_artifacts if "data" in artifact["parts"][0]
    )
    assert fetched_data_artifact["extensions"] == [A2UI_EXTENSION_URI]
    assert fetched_data_artifact["parts"][0]["mediaType"] == A2UI_MEDIA_TYPE


async def test_a2ui_idempotent_retry_replays_same_persisted_task(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        a2ui_enabled=True,
    )
    request = _a2ui_request("a2ui-output-idempotent")

    async with client:
        first = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": A2UI_EXTENSION_URI},
        )
        second = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": A2UI_EXTENSION_URI},
        )

    first_task = first.json()["result"]["task"]
    second_task = second.json()["result"]["task"]
    first_data = _artifact_with_part(first_task, "data")
    second_data = _artifact_with_part(second_task, "data")

    assert first_task["id"] == second_task["id"]
    assert first_data["artifactId"] == second_data["artifactId"] == "a2ui-output"
    assert first_data["extensions"] == second_data["extensions"] == [A2UI_EXTENSION_URI]
    assert first_data["parts"][0]["data"] == second_data["parts"][0]["data"]
    assert len(manager.service.calls) == 1


async def test_a2ui_streaming_subscription_replays_data_artifact_and_terminal_task(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        a2ui_enabled=True,
    )
    request = _a2ui_request("slow-a2ui-output-subscribe")
    request["method"] = "SendStreamingMessage"
    streamed_events: list[dict] = []
    subscription_events: list[dict] = []

    async def collect_subscription(task_id: str) -> None:
        async with client.stream(
            "POST",
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "a2ui-subscribe",
                "method": "SubscribeToTask",
                "params": {"id": task_id},
            },
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    subscription_events.append(json.loads(line[6:]))

    async with client:
        stream_task = asyncio.create_task(
            client.post(
                "/a2a/researcher",
                json=request,
                headers={
                    "Accept": "text/event-stream",
                    "A2A-Extensions": A2UI_EXTENSION_URI,
                },
            )
        )
        await asyncio.wait_for(manager.service.started.wait(), timeout=5)
        duplicate = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": A2UI_EXTENSION_URI},
        )
        duplicate_events = _sse_events_from_text(duplicate.text)
        task_id = duplicate_events[0]["result"]["task"]["id"]
        subscription = asyncio.create_task(collect_subscription(task_id))
        await asyncio.sleep(0.05)
        manager.service.release.set()
        stream_response = await stream_task
        streamed_events.extend(_sse_events_from_text(stream_response.text))
        await subscription
        fetched = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "a2ui-fetch-after-stream",
                "method": "GetTask",
                "params": {"id": task_id},
            },
        )

    stream_updates = [
        event["result"]["artifactUpdate"]
        for event in streamed_events
        if "artifactUpdate" in event.get("result", {})
    ]
    subscription_updates = [
        event["result"]["artifactUpdate"]
        for event in subscription_events
        if "artifactUpdate" in event.get("result", {})
    ]
    subscription_tasks = [
        event["result"]["task"]
        for event in subscription_events
        if "task" in event.get("result", {})
    ]
    fetched_task = fetched.json()["result"]
    fetched_a2ui = _artifact_with_part(fetched_task, "data")

    assert duplicate_events[0]["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
    assert any(update["artifact"]["extensions"] == [A2UI_EXTENSION_URI] for update in stream_updates)
    assert any(
        update["artifact"]["parts"][0]["mediaType"] == A2UI_MEDIA_TYPE
        for update in subscription_updates
    )
    assert subscription_tasks[-1]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert fetched_a2ui["extensions"] == [A2UI_EXTENSION_URI]
    assert fetched_a2ui["parts"][0]["mediaType"] == A2UI_MEDIA_TYPE


async def test_a2ui_task_artifact_and_replay_are_exact_scope_isolated(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    artifact_store = MemoryArtifactStore()
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        artifact_store=artifact_store,
        a2ui_enabled=True,
    )
    request = _a2ui_request("a2ui-output-scoped")

    async with client:
        sent = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": A2UI_EXTENSION_URI},
        )
        task = sent.json()["result"]["task"]
        task_id = task["id"]
        context_id = task["contextId"]
        hidden_get = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "a2ui-hidden-get",
                "method": "GetTask",
                "params": {"id": task_id},
            },
            headers={
                "A2A-Version": "1.0",
                "X-Cognition-Scope-Account": "other",
            },
        )
        hidden_subscribe = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "a2ui-hidden-subscribe",
                "method": "SubscribeToTask",
                "params": {"id": task_id},
            },
            headers={
                "A2A-Version": "1.0",
                "X-Cognition-Scope-Account": "other",
            },
        )

    owner_events = await setup_storage_backend.list_events(
        context_id,
        event_type="artifact.updated",
        task_id=task_id,
        effective_scope={"account": "acme"},
    )
    sibling_events = await setup_storage_backend.list_events(
        context_id,
        event_type="artifact.updated",
        task_id=task_id,
        effective_scope={"account": "other"},
    )

    assert hidden_get.status_code == 404
    assert hidden_subscribe.status_code == 404
    assert owner_events
    assert sibling_events == []
    assert await artifact_store.list_artifacts(scope={"account": "other"}) == []


async def test_a2ui_incompatible_catalog_fails_before_model_execution(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        a2ui_enabled=True,
    )
    request = _send_request("a2ui-output-incompatible")
    request["params"]["message"]["metadata"] = {
        "a2uiRendererCapabilities": {
            "v1.0": {
                "supportedCatalogIds": ["https://example.com/a2ui/catalog.json"],
            }
        }
    }

    async with client:
        response = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": "https://a2ui.org/a2a-extension/a2ui/v1.0"},
        )

    assert "error" in response.json()
    assert "No compatible A2UI catalog" in response.json()["error"]["message"]
    assert "A2A-Extensions" not in response.headers
    assert manager.service.calls == []


async def test_a2ui_renderer_action_and_data_model_continue_model_path(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        a2ui_enabled=True,
    )
    request = _send_request("a2ui-output-action")
    request["params"]["message"]["metadata"] = {
        "a2uiRendererCapabilities": {
            "v1.0": {
                "supportedCatalogIds": [BASIC_CATALOG_ID],
            }
        },
        "a2uiRendererDataModel": {
            "version": "v1.0",
            "surfaces": {"main": {"choice": "approve"}},
        },
    }
    request["params"]["message"]["parts"].append(
        {
            "mediaType": A2UI_MEDIA_TYPE,
            "data": [
                {
                    "version": "v1.0",
                    "action": {
                        "name": "approve",
                        "surfaceId": "main",
                        "sourceComponentId": "approveButton",
                        "timestamp": "2026-08-16T20:00:00Z",
                        "context": {"choice": "approve"},
                        "userMessage": "Approved",
                    },
                }
            ],
        }
    )

    async with client:
        response = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": A2UI_EXTENSION_URI},
        )

    assert response.status_code == 200
    assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(manager.service.calls) == 1


async def test_a2ui_call_agent_function_returns_explicit_error_without_model(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        a2ui_enabled=True,
    )
    request = _send_request("a2ui-function-call")
    request["params"]["message"]["metadata"] = {
        "a2uiRendererCapabilities": {
            "v1.0": {
                "supportedCatalogIds": [BASIC_CATALOG_ID],
            }
        }
    }
    request["params"]["message"]["parts"].append(
        {
            "mediaType": A2UI_MEDIA_TYPE,
            "data": [
                {
                    "version": "v1.0",
                    "callAgentFunction": {
                        "surfaceId": "main",
                        "functionCallId": "call-1",
                        "callFunction": {
                            "call": "openUrl",
                            "catalogId": BASIC_CATALOG_ID,
                            "args": {"url": "https://example.com"},
                        },
                    },
                }
            ],
        }
    )

    async with client:
        response = await client.post(
            "/a2a/researcher",
            json=request,
            headers={"A2A-Extensions": A2UI_EXTENSION_URI},
        )

    task = response.json()["result"]["task"]
    artifact = next(item for item in task["artifacts"] if item["name"] == "a2ui")
    data = artifact["parts"][0]["data"]
    assert response.headers["A2A-Extensions"] == A2UI_EXTENSION_URI
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert artifact["extensions"] == [A2UI_EXTENSION_URI]
    assert data == [
        {
            "version": "v1.0",
            "agentFunctionResponse": {
                "functionCallId": "call-1",
                "error": {
                    "code": "UNKNOWN_AGENT_FUNCTION",
                    "message": (
                        "Cognition does not expose an Agent function registry; "
                        "function 'openUrl' is not available."
                    ),
                },
            },
        }
    ]
    assert manager.service.calls == []


async def test_reusing_message_id_with_different_input_is_rejected(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    first = _send_request("fingerprinted-message")
    conflicting = _send_request("fingerprinted-message")
    conflicting["params"]["message"]["parts"] = [{"text": "Different input"}]

    async with client:
        accepted = await client.post("/a2a/researcher", json=first)
        rejected = await client.post("/a2a/researcher", json=conflicting)

    assert accepted.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert rejected.json()["error"]["code"] == -32602
    assert "different request" in rejected.json()["error"]["message"]


async def test_streaming_tokens_are_coalesced_into_durable_artifact_updates(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        stream_chunk_bytes=10,
    )
    request = _send_request("chunked-output-1")
    request["method"] = "SendStreamingMessage"
    wire_events: list[dict] = []

    async with client:
        async with client.stream("POST", "/a2a/researcher", json=request) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    wire_events.append(json.loads(line[6:]))

    updates = [
        event["result"]["artifactUpdate"]
        for event in wire_events
        if "artifactUpdate" in event.get("result", {})
    ]
    task = next(event["result"]["task"] for event in wire_events if "task" in event["result"])
    text = "".join(
        update["artifact"]["parts"][0].get("text", "") for update in updates
    )
    durable = await setup_storage_backend.list_events(
        task["contextId"],
        event_type="artifact.updated",
        task_id=task["id"],
        effective_scope={"account": "acme"},
    )

    assert text == "one two three four five"
    assert 1 < len(updates) < 5
    assert len(durable) == len(updates)
    assert updates[-1]["lastChunk"] is True


async def test_scope_mismatch_is_not_found_and_v03_method_is_rejected(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        sent = await client.post("/a2a/researcher", json=_send_request())
        task_id = sent.json()["result"]["task"]["id"]
        hidden = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-2",
                "method": "GetTask",
                "params": {"id": task_id},
            },
            headers={
                "A2A-Version": "1.0",
                "X-Cognition-Scope-Account": "other",
            },
        )
        legacy = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-3",
                "method": "message/send",
                "params": {},
            },
        )

        # Agent definition resolution itself conceals scope ownership.
        assert hidden.status_code == 404
        assert legacy.json()["error"]["code"] == -32601


async def test_required_a2a_scope_headers_fail_closed_before_agent_resolution(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        response = await client.post(
            "/a2a/researcher",
            json=_send_request("missing-scope"),
            headers={"X-Cognition-Scope-Account": ""},
        )

    assert response.status_code == 403
    assert response.json() == {
        "error": "Missing required Cognition scope headers",
        "code": "PERMISSION_DENIED",
        "details": {"missing_scope_keys": ["account"]},
    }


async def test_send_streaming_uses_1_0_wrappers_and_terminal_subscribe_is_rejected(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    stream_request = _send_request("stream-message")
    stream_request["method"] = "SendStreamingMessage"
    events: list[dict] = []

    async with client:
        async with client.stream(
            "POST",
            "/a2a/researcher",
            json=stream_request,
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

        assert events
        assert all("result" in event for event in events)
        task_events = [event["result"]["task"] for event in events if "task" in event["result"]]
        task_id = task_events[0]["id"]
        assert any(
            event["result"].get("statusUpdate", {}).get("status", {}).get("state")
            == "TASK_STATE_COMPLETED"
            for event in events
        )

        subscribe = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-subscribe",
                "method": "SubscribeToTask",
                "params": {"id": task_id},
            },
            headers={"Accept": "text/event-stream"},
        )

        assert subscribe.json()["error"]["code"] == -32004


async def test_streaming_execution_timeout_emits_failed_terminal_status(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    stream_request = _send_request("execution-timeout-message")
    stream_request["method"] = "SendStreamingMessage"
    events: list[dict] = []

    async with client:
        async with client.stream(
            "POST",
            "/a2a/researcher",
            json=stream_request,
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    terminal = [
        event["result"]["statusUpdate"]
        for event in events
        if "statusUpdate" in event.get("result", {})
    ]
    assert terminal[-1]["status"]["state"] == "TASK_STATE_FAILED"
    task_id = terminal[-1]["taskId"]
    task = await setup_storage_backend.get_task(task_id, {"account": "acme"})
    assert task is not None
    assert task.status.value == "failed"
    run = await setup_storage_backend.get_run(
        task.last_run_id or task.current_run_id or "",
        {"account": "acme"},
    )
    assert run is not None
    assert run.error_code == "EXECUTION_TIMEOUT"


async def test_jsonrpc_trailing_slash_and_wrong_content_type_are_protocol_responses(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        sent = await client.post("/a2a/researcher/", json=_send_request("slash"))
        wrong_content_type = await client.post(
            "/a2a/researcher",
            content=str(_send_request("wrong-content-type")),
            headers={"Content-Type": "text/plain"},
        )

        assert sent.status_code == 200
        assert sent.history == []
        assert sent.json()["result"]["task"]["status"]["state"] == ("TASK_STATE_COMPLETED")
        assert wrong_content_type.status_code == 200
        assert wrong_content_type.json()["error"]["code"] == -32005


async def test_forward_compatible_unknown_proto_fields_are_ignored(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    request = _send_request("forward-compatible")
    request["params"]["tckExtraParam"] = 42
    request["params"]["message"]["tckUnknownField"] = "ignored"

    async with client:
        response = await client.post("/a2a/researcher", json=request)
        card = await client.get(
            "/a2a/researcher/.well-known/agent-card.json",
        )

    assert response.json()["result"]["task"]["status"]["state"] == ("TASK_STATE_COMPLETED")
    assert "last-modified" in card.headers
    assert card.headers["cache-control"] == "private, max-age=60"


async def test_message_only_and_non_text_artifact_variants_are_supported(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        direct = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-message-response-1"),
        )
        assert direct.json()["result"]["message"]["parts"][0]["text"] == ("Direct message response")

        data = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-artifact-data-object-1"),
        )
        assert _part_value(data.json()["result"]["task"], "data") == {
            "key": "value",
            "count": 42,
        }

        raw = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-artifact-file-1"),
        )
        raw_part = _artifact_with_part(raw.json()["result"]["task"], "raw")["parts"][0]
        assert raw_part["filename"] == "output.txt"
        assert "raw" in raw_part

        url = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-artifact-file-url-1"),
        )
        url_part = _artifact_with_part(url.json()["result"]["task"], "url")["parts"][0]
        assert url_part["url"] == "https://example.com/output.txt"


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        ("object", {"key": "value", "count": 42}),
        ("array", ["proposal", 2]),
        ("string", "proposal"),
        ("number", 7),
        ("boolean", True),
        ("null", None),
    ],
)
async def test_task_projection_preserves_every_data_json_value(
    setup_storage_backend: StorageBackend,
    tmp_path,
    variant: str,
    expected: object,
) -> None:
    """Completed task retrieval must preserve the complete DataPart value union."""
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        sent = await client.post(
            "/a2a/researcher",
            json=_send_request(f"tck-artifact-data-{variant}-task-{tmp_path.name}"),
        )
        task = sent.json()["result"]["task"]
        fetched = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": f"get-{variant}",
                "method": "GetTask",
                "params": {"id": task["id"]},
            },
        )

    assert _part_value(task, "data") == expected
    assert _part_value(fetched.json()["result"], "data") == expected


@pytest.mark.parametrize(
    ("message_id", "variant", "expected"),
    [
        ("stream-output-text", "text", "A2A works"),
        ("tck-artifact-data-object-stream", "data", {"key": "value", "count": 42}),
        ("tck-artifact-data-array-stream", "data", ["proposal", 2]),
        ("tck-artifact-data-string-stream", "data", "proposal"),
        ("tck-artifact-data-number-stream", "data", 7),
        ("tck-artifact-data-boolean-stream", "data", True),
        ("tck-artifact-data-null-stream", "data", None),
        ("tck-artifact-file-stream", "raw", "ZmlsZSBvdXRwdXQ="),
        (
            "tck-artifact-file-url-stream",
            "url",
            "https://example.com/output.txt",
        ),
    ],
)
async def test_all_outbound_part_variants_stream_on_the_a2a_wire(
    setup_storage_backend: StorageBackend,
    tmp_path,
    message_id: str,
    variant: str,
    expected: object,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    request = _send_request(message_id)
    request["method"] = "SendStreamingMessage"
    events: list[dict] = []

    async with client:
        async with client.stream(
            "POST",
            "/a2a/researcher",
            json=request,
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    artifact_updates = [
        event["result"]["artifactUpdate"]
        for event in events
        if "artifactUpdate" in event.get("result", {})
    ]
    assert len(artifact_updates) == 1
    part = artifact_updates[0]["artifact"]["parts"][0]
    assert part[variant] == expected
    assert artifact_updates[0]["lastChunk"] is True
    assert any(
        event["result"].get("statusUpdate", {}).get("status", {}).get("state")
        == "TASK_STATE_COMPLETED"
        for event in events
    )


@pytest.mark.parametrize(
    "value",
    [
        {"proposal": "one"},
        ["proposal", 2],
        "proposal",
        7,
        True,
        None,
    ],
)
def test_data_artifact_replay_preserves_every_json_value(value: object) -> None:
    """Reconnect replay must preserve the full A2A DataPart value union."""
    event = _artifact_update_from_runtime_event(
        "task-1",
        "context-1",
        {
            "artifact_id": "data-1",
            "name": "response",
            "kind": "data",
            "value": value,
            "media_type": "application/json",
            "last_chunk": True,
        },
    )

    assert event is not None
    assert event.artifact.parts[0].WhichOneof("content") == "data"
    assert MessageToDict(event.artifact.parts[0].data) == value


async def test_all_inbound_part_variants_are_ordered_scoped_and_inert(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    artifact_store = MemoryArtifactStore()
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        artifact_store,
        input_modes=["text/plain", "application/json", "application/pdf"],
    )
    request = _send_request("all-input-parts")
    request["params"]["message"].update(
        {
            "extensions": ["https://example.com/decision-room/v1"],
            "metadata": {"roomId": "room-1"},
            "referenceTaskIds": ["task-parent"],
        }
    )
    request["params"]["message"]["parts"] = [
        {
            "text": "Analyze",
            "mediaType": "text/plain",
            "metadata": {"schema": {"type": "object"}},
        },
        {
            "data": {"priority": 3},
            "mediaType": "application/json",
            "metadata": {"contractVersion": "1.0"},
        },
        {
            "raw": "aGVsbG8=",
            "filename": "../../note.txt",
            "mediaType": "text/plain",
        },
        {
            "url": "https://example.com/report.pdf",
            "filename": "report.pdf",
            "mediaType": "application/pdf",
        },
    ]

    async with client:
        response = await client.post("/a2a/researcher", json=request)
        retry = await client.post("/a2a/researcher", json=request)

    assert response.json()["result"]["task"]["status"]["state"] == ("TASK_STATE_COMPLETED")
    assert retry.json()["result"]["task"]["id"] == (response.json()["result"]["task"]["id"])
    assert len(manager.service.calls) == 1
    content = manager.service.calls[-1]["content"]
    assert content.index("Analyze") < content.index("A2A data Part 1")
    assert content.index("A2A data Part 1") < content.index("A2A raw Part 2")
    assert content.index("A2A raw Part 2") < content.index("A2A url Part 3")
    assert "decision-room/v1" in content
    assert '"roomId": "room-1"' in content
    assert '"schema": {"type": "object"}' in content

    task_id = response.json()["result"]["task"]["id"]
    stored_task = await setup_storage_backend.get_task(
        task_id,
        {"account": "acme"},
        "researcher",
    )
    assert stored_task is not None
    assert [part["kind"] for part in stored_task.metadata["input_parts"]] == [
        "text",
        "data",
        "raw",
        "url",
    ]
    assert stored_task.metadata["input_parts"][0]["metadata"] == {
        "schema": {"type": "object"}
    }
    assert stored_task.metadata["input_parts"][1]["value"] == {"priority": 3.0}
    assert stored_task.metadata["input_messages"] == [
        {
            "message_id": "all-input-parts",
            "part_ids": [part["part_id"] for part in stored_task.metadata["input_parts"]],
            "metadata": {"roomId": "room-1"},
            "extensions": ["https://example.com/decision-room/v1"],
            "reference_task_ids": ["task-parent"],
        }
    ]

    artifacts = await artifact_store.list_artifacts(scope={"account": "acme"})
    artifacts = [item for item in artifacts if item.id.startswith("a2a-input-")]
    assert [(item.content_type, item.content) for item in artifacts] == [
        ("text/plain", "aGVsbG8="),
        ("application/pdf", "https://example.com/report.pdf"),
    ]
    assert await artifact_store.list_artifacts(scope={"account": "other"}) == []
    assert all(item.name.startswith("a2a-input-") for item in artifacts)


async def test_oversized_raw_input_fails_before_task_or_model_execution(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
        max_raw_part_bytes=4,
    )
    request = _send_request("oversized-input")
    request["params"]["message"]["parts"] = [{"raw": "aGVsbG8="}]

    async with client:
        response = await client.post("/a2a/researcher", json=request)

    assert "error" in response.json()
    assert manager.service.calls == []


@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
async def test_unsupported_media_type_fails_before_task_or_model_execution(
    setup_storage_backend: StorageBackend,
    tmp_path,
    method: str,
) -> None:
    manager = _FakeSessionAgentManager(setup_storage_backend)
    client = await _build_client(
        setup_storage_backend,
        tmp_path,
        manager,
    )
    request = _send_request(f"unsupported-media-{method}")
    request["method"] = method
    request["params"]["message"]["parts"] = [
        {
            "raw": "dGNr",
            "mediaType": "application/x-unsupported-tck-type",
        }
    ]

    async with client:
        response = await client.post("/a2a/researcher", json=request)

    body = response.json()
    assert body["error"]["code"] == -32005
    assert "unsupported media type" in body["error"]["message"]
    assert manager.service.calls == []
    tasks, cursor = await setup_storage_backend.list_tasks(
        "researcher",
        {"account": "acme"},
    )
    assert tasks == []
    assert cursor is None


async def test_input_required_continues_same_task_with_new_attempt_and_can_cancel(
    setup_storage_backend: StorageBackend,
    tmp_path,
) -> None:
    client = await _build_client(setup_storage_backend, tmp_path)
    async with client:
        interrupted = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-input-required-1"),
        )
        task = interrupted.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

        followup = _send_request("tck-complete-task-followup")
        followup["params"]["message"].update({"taskId": task["id"], "contextId": task["contextId"]})
        completed = await client.post("/a2a/researcher", json=followup)
        completed_task = completed.json()["result"]["task"]
        assert completed_task["id"] == task["id"]
        assert completed_task["status"]["state"] == "TASK_STATE_COMPLETED"
        runs = await setup_storage_backend.list_runs(
            task["contextId"],
            {"account": "acme"},
        )
        assert [run.attempt for run in reversed(runs)] == [1, 2]

        terminal_followup = _send_request("terminal-followup")
        terminal_followup["params"]["message"].update(
            {"taskId": task["id"], "contextId": task["contextId"]}
        )
        rejected = await client.post("/a2a/researcher", json=terminal_followup)
        assert rejected.json()["error"]["code"] == -32004

        cancel = await client.post(
            "/a2a/researcher",
            json={
                "jsonrpc": "2.0",
                "id": "rpc-cancel",
                "method": "CancelTask",
                "params": {"id": task["id"]},
            },
        )
        assert cancel.json()["error"]["code"] == -32002
