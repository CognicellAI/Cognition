"""Live-model A2A/A2UI end-to-end scenario.

This test is intentionally opt-in through live OpenAI-compatible credentials.
It proves the full runtime path that mocked protocol tests cannot: an A2UI
Agent negotiates the A2UI extension, receives typed structured model output,
returns text plus renderable A2UI data, accepts a renderer action, and emits an
updated A2UI surface.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from server.app.protocols.a2a.a2ui import A2UI_EXTENSION_URI, A2UI_MEDIA_TYPE, BASIC_CATALOG_ID

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.asyncio,
    pytest.mark.timeout(180),
    pytest.mark.skipif(
        not os.environ.get("COGNITION_OPENAI_COMPATIBLE_API_KEY"),
        reason=(
            "Requires COGNITION_OPENAI_COMPATIBLE_API_KEY for the live-model "
            "A2UI scenario."
        ),
    ),
]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest_asyncio.fixture
async def live_a2ui_server() -> AsyncIterator[str]:
    """Start a temporary Cognition server wired to a live OpenAI-compatible model."""
    port = _find_free_port()
    metrics_port = _find_free_port()
    model = os.environ.get("COGNITION_A2UI_LIVE_MODEL", "openai/gpt-4.1-mini")
    base_url = os.environ.get(
        "COGNITION_A2UI_LIVE_BASE_URL",
        os.environ.get("COGNITION_OPENAI_COMPATIBLE_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    with tempfile.TemporaryDirectory(prefix="cognition-a2ui-live-") as workspace:
        env = os.environ.copy()
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_LOCAL_WORKSPACE_ROOT"] = workspace
        env["COGNITION_LLM_PROVIDER"] = "openai_compatible"
        env["COGNITION_LLM_MODEL"] = model
        env["COGNITION_OPENAI_COMPATIBLE_BASE_URL"] = base_url
        env["COGNITION_METRICS_PORT"] = str(metrics_port)
        env["COGNITION_OTEL_ENABLED"] = "false"
        env["COGNITION_ALLOW_UNSAFE_LOCAL_EXECUTION"] = "true"

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(Path(__file__).parent.parent.parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        server = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        while time.time() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    "Live A2UI server exited early. "
                    f"stdout={stdout.decode()[-1000:]} stderr={stderr.decode()[-1000:]}"
                )
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(f"{server}/ready")
                    if response.status_code == 200 and response.json().get("ready"):
                        break
            except httpx.HTTPError:
                await asyncio.sleep(0.2)
        else:
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            raise RuntimeError(
                "Live A2UI server failed to start. "
                f"stdout={stdout.decode()[-1000:]} stderr={stderr.decode()[-1000:]}"
            )

        yield server

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def test_live_model_returns_a2ui_surface_and_updates_after_renderer_action(
    live_a2ui_server: str,
) -> None:
    """Exercise text plus A2UI output and renderer action continuation."""
    agent_name = f"a2ui-live-{uuid.uuid4().hex[:8]}"
    provider_model = os.environ.get("COGNITION_A2UI_LIVE_MODEL", "openai/gpt-4.1-mini")
    provider_base_url = os.environ.get(
        "COGNITION_A2UI_LIVE_BASE_URL",
        os.environ.get("COGNITION_OPENAI_COMPATIBLE_BASE_URL", "https://openrouter.ai/api/v1"),
    )

    async with httpx.AsyncClient(base_url=live_a2ui_server, timeout=90.0) as client:
        provider = await client.post(
            "/models/providers",
            json={
                "id": "a2ui-live-provider",
                "provider": "openai_compatible",
                "model": provider_model,
                "display_name": "A2UI live provider",
                "enabled": True,
                "priority": 0,
                "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                "base_url": provider_base_url,
                "timeout": 75,
                "max_retries": 1,
            },
        )
        assert provider.status_code in {200, 201}, provider.text

        created = await client.post(
            "/agents",
            json={
                "name": agent_name,
                "system_prompt": (
                    "You are testing Cognition A2UI support. For A2UI requests, "
                    "produce a compact Basic-catalog surface. Use a stable "
                    "surfaceId of main, prefer one flat Text component, and update "
                    "the surface when the renderer sends an action."
                ),
                "description": "Live A2UI E2E Agent",
                "mode": "primary",
                "temperature": 0,
                "a2a": {
                    "exposed": True,
                    "a2ui": {"version": "1.0", "catalogs": ["basic"]},
                },
            },
        )
        assert created.status_code in {200, 201}, created.text

        card = await client.get(f"/a2a/{agent_name}/.well-known/agent-card.json")
        assert card.status_code == 200, card.text
        card_body = card.json()
        assert A2UI_MEDIA_TYPE in card_body["defaultInputModes"]
        assert A2UI_MEDIA_TYPE in card_body["defaultOutputModes"]

        initial = await client.post(
            f"/a2a/{agent_name}",
            json=_send_message(
                message_id=f"initial-{uuid.uuid4()}",
                text=(
                    "Create a tiny text-only status panel for release v0.15.0."
                ),
            ),
            headers=_a2a_headers(),
        )
        assert initial.status_code == 200, initial.text
        assert "result" in initial.json(), initial.text
        assert initial.headers.get("A2A-Extensions") == A2UI_EXTENSION_URI

        initial_task = initial.json()["result"]["task"]
        text_parts = _parts(initial_task, "text")
        a2ui_parts = _parts(initial_task, "data")
        assert initial_task["status"]["state"] == "TASK_STATE_COMPLETED", json.dumps(
            initial_task["status"], indent=2
        )
        assert any(part["text"].strip() for part in text_parts)
        assert a2ui_parts, initial_task
        assert all(part["mediaType"] == A2UI_MEDIA_TYPE for part in a2ui_parts)
        initial_messages = a2ui_parts[-1]["data"]
        assert isinstance(initial_messages, list)
        assert _contains_agent_to_renderer_message(initial_messages, "createSurface")

        action = await client.post(
            f"/a2a/{agent_name}",
            json=_send_message(
                message_id=f"action-{uuid.uuid4()}",
                text="The renderer action selected approval. Update the existing surface.",
                parts=[
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
                ],
                metadata={
                    "a2uiRendererDataModel": {
                        "version": "v1.0",
                        "surfaces": {"main": {"status": "approved"}},
                    }
                },
            ),
            headers=_a2a_headers(),
        )
        assert action.status_code == 200, action.text
        assert "result" in action.json(), action.text
        assert action.headers.get("A2A-Extensions") == A2UI_EXTENSION_URI

        action_task = action.json()["result"]["task"]
        action_parts = _parts(action_task, "data")
        assert action_task["status"]["state"] == "TASK_STATE_COMPLETED", json.dumps(
            action_task["status"], indent=2
        )
        assert action_parts, action_task
        action_messages = action_parts[-1]["data"]
        assert isinstance(action_messages, list)
        assert _contains_agent_to_renderer_message(action_messages, "updateComponents") or (
            _contains_agent_to_renderer_message(action_messages, "updateDataModel")
        )

        fetched = await client.post(
            f"/a2a/{agent_name}",
            json={
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "GetTask",
                "params": {"id": action_task["id"]},
            },
            headers=_a2a_headers(),
        )
        assert fetched.status_code == 200, fetched.text
        assert _parts(fetched.json()["result"], "data")[-1]["mediaType"] == A2UI_MEDIA_TYPE


def _send_message(
    *,
    message_id: str,
    text: str,
    parts: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_metadata: dict[str, Any] = {
        "a2uiRendererCapabilities": {
            "v1.0": {
                "supportedCatalogIds": [BASIC_CATALOG_ID],
            }
        }
    }
    if metadata:
        message_metadata.update(metadata)

    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": message_id,
                "role": "ROLE_USER",
                "parts": [{"text": text, "mediaType": "text/plain"}, *(parts or [])],
                "metadata": message_metadata,
            }
        },
    }


def _a2a_headers() -> dict[str, str]:
    return {
        "A2A-Version": "1.0",
        "A2A-Extensions": A2UI_EXTENSION_URI,
    }


def _parts(task: dict[str, Any], content_key: str) -> list[dict[str, Any]]:
    return [
        part
        for artifact in task.get("artifacts", [])
        for part in artifact.get("parts", [])
        if content_key in part
    ]


def _contains_agent_to_renderer_message(messages: list[Any], message_type: str) -> bool:
    return any(isinstance(message, dict) and message_type in message for message in messages)
