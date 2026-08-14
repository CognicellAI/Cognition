"""Deterministic, test-only Cognition SUT for the official A2A 1.0 TCK.

The message-ID prefix behaviors mirror the official TCK's Gherkin SUT
scenarios. They validate Cognition's real adapter, persistence, and streaming
paths without depending on a production LLM agent. They are never mounted by
the Cognition application.

Run with::

    uv run uvicorn tests.support.a2a_tck_sut:create_app --factory --port 9999
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI

from server.app.agent.runtime import (
    ArtifactEvent,
    DirectMessageEvent,
    DoneEvent,
    InterruptEvent,
    RejectedEvent,
    TokenEvent,
)
from server.app.protocols.a2a.routes import mount_a2a_routes
from server.app.settings import Settings
from server.app.storage.artifact_store import MemoryArtifactStore
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore
from server.app.storage.memory import MemoryStorageBackend
from server.version import VERSION


class _TckService:
    def __init__(self, store: MemoryStorageBackend) -> None:
        self._store = store

    async def stream_response(self, **kwargs: Any):
        messages = await self._store.list_messages_for_session(kwargs["session_id"])
        message_id = str((messages[-1].metadata or {}).get("a2a_message_id") or messages[-1].id)

        if message_id.startswith("tck-input-required"):
            yield InterruptEvent(tool_call_id="approval", tool_name="confirm", args={})
            return
        if message_id.startswith("tck-reject-task"):
            yield RejectedEvent(reason="rejected")
            return
        if message_id.startswith("tck-message-response"):
            yield DirectMessageEvent(content="Direct message response")
            return
        if message_id.startswith("tck-artifact-text"):
            yield _artifact("text", "Generated text content")
            yield DoneEvent()
            return
        if message_id.startswith("tck-artifact-file-url"):
            yield _artifact(
                "url",
                "https://example.com/output.txt",
                filename="output.txt",
            )
            yield DoneEvent()
            return
        if message_id.startswith("tck-artifact-file"):
            yield _artifact("raw", b"file output", filename="output.txt")
            yield DoneEvent()
            return
        if message_id.startswith("tck-artifact-data"):
            yield _artifact("data", {"key": "value", "count": 42})
            yield DoneEvent()
            return
        if message_id.startswith("tck-stream-artifact-chunked"):
            yield _artifact("text", "chunk-1 ", last_chunk=False)
            yield _artifact("text", "chunk-2", append=True)
            yield DoneEvent()
            return
        if message_id.startswith("tck-stream-artifact-file"):
            yield _artifact("raw", b"file output", filename="output.txt")
            yield DoneEvent()
            return
        streaming_text = _streaming_text(message_id)
        if streaming_text is not None:
            yield _artifact("text", streaming_text)
            yield DoneEvent()
            return
        if message_id.startswith("test-resubscribe-message-id"):
            await asyncio.sleep(5)
            yield DoneEvent()
            return

        yield TokenEvent(content="Hello from TCK")
        yield DoneEvent()


class _TckManager:
    def __init__(self, store: MemoryStorageBackend) -> None:
        self._service = _TckService(store)

    def get_service(self, _session_id: str) -> _TckService:
        return self._service

    def register_session(self, _session_id: str, _workspace: str) -> _TckService:
        return self._service

    async def abort_session(self, _session_id: str, _thread_id: str) -> bool:
        return True


def create_app() -> FastAPI:
    """Create a fresh Cognition adapter instance for one TCK run."""
    workspace = Path(tempfile.mkdtemp(prefix="cognition-a2a-tck-"))
    store = MemoryStorageBackend(workspace_path=str(workspace))
    config_store = DefaultConfigStore(MemoryConfigRegistry())
    artifact_store = MemoryArtifactStore()

    settings = Settings.model_validate(
        {
            "COGNITION_LOCAL_WORKSPACE_ROOT": workspace,
            "COGNITION_SCOPE_KEYS": [],
        }
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await store.initialize()
        await config_store.upsert_agent(
            "tck-agent",
            {},
            {
                "name": "tck-agent",
                "system_prompt": "A2A TCK system under test",
                "description": "Cognition A2A 1.0 conformance agent",
                "mode": "primary",
                "a2a": {"exposed": True},
            },
        )
        await mount_a2a_routes(
            app,
            settings=settings,
            config_store=config_store,
            session_agent_manager=cast(Any, _TckManager(store)),
            store=store,
            version=VERSION,
            artifact_store=artifact_store,
            # The A2A specification makes SendMessage idempotency optional and
            # the TCK intentionally reuses message IDs across independent tests.
            # Disable the optional extension only for this conformance fixture.
            message_id_idempotency=False,
        )
        yield

    app = FastAPI(lifespan=lifespan)
    return app


def _artifact(
    kind: str,
    value: Any,
    *,
    filename: str | None = None,
    append: bool = False,
    last_chunk: bool = True,
) -> ArtifactEvent:
    return ArtifactEvent(
        artifact_id="tck-output",
        name="response",
        kind=kind,  # type: ignore[arg-type]
        value=value,
        media_type="application/json" if kind == "data" else "text/plain",
        filename=filename,
        append=append,
        last_chunk=last_chunk,
    )


def _streaming_text(message_id: str) -> str | None:
    values = {
        "tck-stream-001": "Stream hello from TCK",
        "tck-stream-003": "Stream task lifecycle",
        "tck-stream-ordering-001": "Ordered output",
        "tck-stream-artifact-text": "Streamed text content",
    }
    return next((value for prefix, value in values.items() if message_id.startswith(prefix)), None)
