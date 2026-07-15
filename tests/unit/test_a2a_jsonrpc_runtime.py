"""End-to-end unit tests for the strict A2A 1.0 JSON-RPC facade."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import httpx
from fastapi import FastAPI

from server.app.agent.runtime import (
    ArtifactEvent,
    DirectMessageEvent,
    DoneEvent,
    InterruptEvent,
    TokenEvent,
)
from server.app.llm.deep_agent_service import SessionAgentManager
from server.app.protocols.a2a.routes import mount_a2a_routes
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

    async def stream_response(self, **kwargs):
        messages = await self._store.list_messages_for_session(kwargs["session_id"])
        message_id = messages[-1].id
        if message_id.startswith("slow-message"):
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
        if message_id.startswith("tck-artifact-data"):
            yield ArtifactEvent(
                artifact_id="data-output",
                name="response",
                kind="data",
                value={"key": "value", "count": 42},
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
            "a2a_exposed": True,
        },
    )
    artifact_store = MemoryArtifactStore()

    class _Settings:
        scope_keys = ["account"]
        scoping_enabled = True
        workspace_path = tmp_path

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
        await asyncio.wait_for(manager.service.started.wait(), timeout=1)

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
            json=_send_request("tck-artifact-data-1"),
        )
        assert data.json()["result"]["task"]["artifacts"][0]["parts"][0]["data"] == {
            "key": "value",
            "count": 42,
        }

        raw = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-artifact-file-1"),
        )
        raw_part = raw.json()["result"]["task"]["artifacts"][0]["parts"][0]
        assert raw_part["filename"] == "output.txt"
        assert "raw" in raw_part

        url = await client.post(
            "/a2a/researcher",
            json=_send_request("tck-artifact-file-url-1"),
        )
        url_part = url.json()["result"]["task"]["artifacts"][0]["parts"][0]
        assert url_part["url"] == "https://example.com/output.txt"


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
        runs = await setup_storage_backend.list_runs(task["contextId"])
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
