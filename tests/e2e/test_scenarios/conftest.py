"""Pytest fixtures and utilities for P2 scenario tests.

This module provides shared fixtures and helper functions for all P2 scenario tests.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from tests.e2e.conftest import (
    E2E_DEFAULT_AGENT_NAME,
    E2E_READONLY_AGENT_NAME,
    ensure_e2e_agent,
    ensure_e2e_provider,
)


@pytest.fixture(autouse=True)
async def setup_storage_backend():
    """Override the parent conftest storage fixture to use live server.

    The parent tests/conftest.py has an autouse fixture that sets up a test
    SQLite database, which prevents e2e tests from hitting the real server.
    This fixture overrides that behavior so e2e tests test against the
    live docker-compose environment.
    """
    # Do nothing - let the tests use the real server
    yield None


# Default test configuration — override with COGNITION_E2E_URL env var
DEFAULT_BASE_URL = "http://localhost:8000"
BASE_URL = os.environ.get("COGNITION_E2E_URL", DEFAULT_BASE_URL).rstrip("/")
TEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def is_terminal_stream_event(event: dict[str, Any]) -> bool:
    """Return whether an SSE event represents stream termination.

    Normal message streams can use ``run_state`` with ``to_status=idle`` as
    the per-run completion signal. Some older endpoints and resume flows still
    emit a standalone ``done`` event, and errors are terminal for both shapes.
    """
    event_type = event.get("event")
    if event_type in {"done", "error"}:
        return True
    return event_type == "run_state" and event.get("to_status") in {
        "idle",
        "done",
        "failed",
        "aborted",
    }


def stream_completed(events: list[dict[str, Any]]) -> bool:
    """Return whether collected SSE events include a terminal event."""
    return any(is_terminal_stream_event(event) for event in events)


class ScenarioTestClient:
    """Test client for P2 scenarios with helper methods."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=TEST_TIMEOUT)
        self.scope_header: dict[str, str] = {}
        self.default_agent_name = E2E_DEFAULT_AGENT_NAME

    async def check_scoping(self) -> bool:
        """Check if session scoping is enabled."""
        try:
            response = await self.client.get(f"{self.base_url}/config")
            if response.status_code == 200:
                config = response.json()
                return config.get("server", {}).get("scoping_enabled", False)
        except Exception:
            pass
        return False

    async def check_available(self) -> bool:
        """Return whether the external scenario server is reachable."""
        try:
            response = await self.client.get(f"{self.base_url}/ready")
            return response.status_code in {200, 503}
        except httpx.HTTPError:
            return False

    async def setup_scoping(self) -> None:
        """Setup scoping header if enabled."""
        if await self.check_scoping():
            self.scope_header = {"X-Cognition-Scope-User": "test-user"}

    async def ensure_test_agents(self) -> None:
        """Provision shared builder-owned Agents used by the scenario suite."""
        await ensure_e2e_provider(
            self.client,
            self.base_url,
            headers=self.scope_header,
        )
        await ensure_e2e_agent(
            self.client,
            self.base_url,
            E2E_DEFAULT_AGENT_NAME,
            headers=self.scope_header,
        )
        await ensure_e2e_agent(
            self.client,
            self.base_url,
            E2E_READONLY_AGENT_NAME,
            headers=self.scope_header,
        )

    async def use_scope(self, headers: dict[str, str]) -> None:
        """Switch to an exact test scope and provision fixture Agents there."""
        self.scope_header = dict(headers)
        await self.ensure_test_agents()

    async def get(self, path: str, **kwargs) -> httpx.Response:
        """Make GET request with optional scoping."""
        headers = {**self.scope_header, **kwargs.pop("headers", {})}
        return await self.client.get(f"{self.base_url}{path}", headers=headers, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx.Response:
        """Make POST request with optional scoping."""
        headers = {**self.scope_header, **kwargs.pop("headers", {})}
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        if path == "/sessions" and isinstance(kwargs.get("json"), dict):
            payload = dict(kwargs["json"])
            payload.setdefault("agent_name", self.default_agent_name)
            kwargs["json"] = payload
            if payload["agent_name"] in {E2E_DEFAULT_AGENT_NAME, E2E_READONLY_AGENT_NAME}:
                await self.ensure_test_agents()
        return await self.client.post(f"{self.base_url}{path}", headers=headers, **kwargs)

    async def patch(self, path: str, **kwargs) -> httpx.Response:
        """Make PATCH request with optional scoping."""
        headers = {**self.scope_header, **kwargs.pop("headers", {})}
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        return await self.client.patch(f"{self.base_url}{path}", headers=headers, **kwargs)

    async def put(self, path: str, **kwargs) -> httpx.Response:
        """Make PUT request with optional scoping."""
        headers = {**self.scope_header, **kwargs.pop("headers", {})}
        if "json" in kwargs:
            headers["Content-Type"] = "application/json"
        return await self.client.put(f"{self.base_url}{path}", headers=headers, **kwargs)

    async def delete(self, path: str, **kwargs) -> httpx.Response:
        """Make DELETE request with optional scoping."""
        headers = {**self.scope_header, **kwargs.pop("headers", {})}
        return await self.client.delete(f"{self.base_url}{path}", headers=headers, **kwargs)

    async def stream_sse(
        self, path: str, data: dict[str, Any], max_events: int = 100, timeout: float = 15.0
    ) -> list[str]:
        """Stream SSE events from an endpoint."""
        headers = {
            **self.scope_header,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        events = []
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}{path}",
                json=data,
                headers=headers,
                timeout=httpx.Timeout(timeout),
            ) as response:
                async for line in response.aiter_lines():
                    events.append(line)
                    if len(events) >= max_events:
                        break
        except Exception:
            pass

        match = re.match(r"^/sessions/([^/]+)/messages$", path)
        if match:
            await self.wait_for_session_idle(match.group(1))

        return events

    async def wait_for_session_idle(self, session_id: str, timeout: float = 10.0) -> None:
        """Wait until the session is stably ready for another turn."""
        deadline = asyncio.get_running_loop().time() + timeout
        observed_idle = False
        while True:
            response = await self.get(f"/sessions/{session_id}")
            is_idle = response.status_code == 200 and response.json().get("status") == "idle"
            if is_idle and observed_idle:
                return
            observed_idle = is_idle
            if asyncio.get_running_loop().time() >= deadline:
                return
            await asyncio.sleep(0.1)

    async def create_session(
        self,
        title: str = "Test Session",
        agent_name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Create a new session and return its ID."""
        payload: dict[str, Any] = {"title": title}
        payload["agent_name"] = agent_name or self.default_agent_name
        if metadata is not None:
            payload["metadata"] = metadata
        response = await self.post("/sessions", json=payload)
        assert response.status_code == 201, f"Failed to create session: {response.status_code}"
        return response.json()["id"]

    async def send_message(
        self, session_id: str, content: str, stream: bool = False
    ) -> httpx.Response | list[str]:
        """Send a message to a session."""
        if stream:
            return await self.stream_sse(f"/sessions/{session_id}/messages", {"content": content})
        else:
            await self.wait_for_session_idle(session_id)
            response = await self.post(
                f"/sessions/{session_id}/messages",
                json={"content": content},
                headers={**self.scope_header, "Accept": "application/json"},
            )
            if response.status_code == 409:
                await self.wait_for_session_idle(session_id)
                response = await self.post(
                    f"/sessions/{session_id}/messages",
                    json={"content": content},
                    headers={**self.scope_header, "Accept": "application/json"},
                )
            return response

    async def get_messages(self, session_id: str, **params) -> list[dict[str, Any]]:
        """Get messages for a session."""
        response = await self.get(f"/sessions/{session_id}/messages", params=params)
        assert response.status_code == 200
        return response.json().get("messages", [])

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


@pytest_asyncio.fixture
async def api_client() -> AsyncGenerator[ScenarioTestClient, None]:
    """Fixture providing a configured API test client."""
    client = ScenarioTestClient()
    if not await client.check_available():
        await client.close()
        pytest.skip(
            "Scenario E2E server is not reachable; set COGNITION_E2E_URL or "
            "start the docker-compose E2E environment."
        )
    await client.setup_scoping()
    await client.ensure_test_agents()
    yield client
    await client.close()


@pytest_asyncio.fixture
async def session(api_client: ScenarioTestClient) -> str:
    """Fixture providing a new session ID."""
    return await api_client.create_session("Test Session")


class TestTimer:
    """Simple timer for performance testing."""

    def __init__(self) -> None:
        self.start_time: float = 0.0
        self.end_time: float = 0.0

    def start(self) -> None:
        """Start the timer."""
        self.start_time = time.time()

    def stop(self) -> float:
        """Stop the timer and return duration in milliseconds."""
        self.end_time = time.time()
        return (self.end_time - self.start_time) * 1000

    @property
    def duration_ms(self) -> float:
        """Get duration in milliseconds."""
        if self.end_time > 0:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000


@pytest.fixture
def timer() -> TestTimer:
    """Fixture providing a performance timer."""
    return TestTimer()
