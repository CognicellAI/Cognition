"""E2E test fixtures and utilities."""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator, Generator

import httpx
import pytest
import pytest_asyncio
import websockets


# ============================================================================
# CLEANUP HELPERS
# ============================================================================


async def cleanup_test_projects(api_client: httpx.AsyncClient, project_names: list[str]) -> None:
    """Delete test projects by name prefix.

    Args:
        api_client: AsyncClient for API calls.
        project_names: List of project prefixes to delete.
    """
    try:
        # List all projects
        response = await api_client.get("/api/projects")
        projects = response.json().get("projects", [])

        # Find and delete test projects
        for project_name in project_names:
            for project in projects:
                if project_name in project.get("user_prefix", ""):
                    # Try to delete (note: Cognition may not have delete endpoint)
                    # For now, we'll just log the cleanup intent
                    pass
    except Exception as e:
        print(f"Warning: Failed to cleanup projects: {e}")


def cleanup_test_containers() -> None:
    """Stop and remove Docker containers created by tests.

    Cleans up any cognition-agent containers that were created during testing.
    """
    try:
        # Find all cognition-agent containers
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "ancestor=cognition-agent:latest",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        container_ids = result.stdout.strip().split("\n")
        container_ids = [cid for cid in container_ids if cid]  # Filter empty strings

        if not container_ids:
            return

        # Stop containers
        for container_id in container_ids:
            try:
                subprocess.run(
                    ["docker", "stop", container_id],
                    capture_output=True,
                    timeout=10,
                )
            except Exception as e:
                print(f"Warning: Failed to stop container {container_id}: {e}")

        # Remove containers
        for container_id in container_ids:
            try:
                subprocess.run(
                    ["docker", "rm", container_id],
                    capture_output=True,
                    timeout=10,
                )
            except Exception as e:
                print(f"Warning: Failed to remove container {container_id}: {e}")

    except Exception as e:
        print(f"Warning: Failed to cleanup containers: {e}")


@pytest.fixture(scope="session")
def server_process() -> Generator[subprocess.Popen, None, None]:
    """Start the Cognition server for E2E tests on port 9000.

    This fixture starts the FastAPI server in a subprocess and ensures
    it's ready to accept connections before yielding.
    Uses port 9000 to avoid conflicts with other services.
    """
    # Start server on port 9000
    env = os.environ.copy()
    env["LOG_LEVEL"] = "info"

    process = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"],
        cwd="/Users/dubh3124/workspace/cognition/server",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready on port 9000
    max_attempts = 30
    attempt = 0
    while attempt < max_attempts:
        try:
            response = subprocess.run(
                ["curl", "-s", "http://localhost:9000/health"],
                capture_output=True,
                timeout=1,
            )
            if response.returncode == 0:
                break
        except Exception:
            pass

        time.sleep(0.5)
        attempt += 1

    if attempt >= max_attempts:
        process.terminate()
        raise RuntimeError("Server failed to start within 15 seconds")

    yield process

    # Cleanup
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest_asyncio.fixture
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create an async HTTP client for API calls to port 9000."""
    client = httpx.AsyncClient(base_url="http://localhost:9000/api")
    yield client
    await client.aclose()


@pytest.fixture
def cleanup_after_test():
    """Cleanup fixture that runs after each test.

    Automatically removes Docker containers created during testing.
    """
    # Before test
    yield
    # After test - cleanup
    cleanup_test_containers()


@pytest_asyncio.fixture
async def ws_connection():
    """Create a WebSocket connection to the server on port 9000.

    Usage:
        async def test_websocket(ws_connection):
            ws = ws_connection
            await ws.send(json.dumps({"event": "user_message", ...}))
            message = await ws.recv()
    """
    uri = "ws://localhost:9000/ws"
    ws = await websockets.connect(uri)
    yield ws
    await ws.close()


@pytest.fixture
def temp_project_name() -> str:
    """Generate a unique project name for testing."""
    import time
    import random

    timestamp = int(time.time() * 1000)
    random_suffix = random.randint(1000, 9999)
    return f"e2e-test-{timestamp}-{random_suffix}"


# ============================================================================
# E2E TEST HELPERS
# ============================================================================


async def create_project(api_client: httpx.AsyncClient, name: str) -> dict:
    """Helper to create a project via API.

    Args:
        api_client: AsyncClient for API calls.
        name: Project name/prefix.

    Returns:
        Project response dict.
    """
    response = await api_client.post(
        "/projects",
        json={
            "user_prefix": name,
            "network_mode": "OFF",
        },
    )
    response.raise_for_status()
    return response.json()


async def get_project(api_client: httpx.AsyncClient, project_id: str) -> dict:
    """Helper to get project details.

    Args:
        api_client: AsyncClient for API calls.
        project_id: Project ID.

    Returns:
        Project details dict.
    """
    response = await api_client.get(f"/projects/{project_id}")
    response.raise_for_status()
    return response.json()


async def list_projects(api_client: httpx.AsyncClient) -> dict:
    """Helper to list projects.

    Args:
        api_client: AsyncClient for API calls.

    Returns:
        List projects response dict.
    """
    response = await api_client.get("/projects")
    response.raise_for_status()
    return response.json()


async def send_ws_message(
    ws,
    message: dict,
) -> None:
    """Helper to send a WebSocket message.

    Args:
        ws: WebSocket connection.
        message: Message dict to send.
    """
    await ws.send(json.dumps(message))


async def receive_ws_event(
    ws,
    timeout: float = 5.0,
) -> dict:
    """Helper to receive a WebSocket event with timeout.

    Args:
        ws: WebSocket connection.
        timeout: Timeout in seconds.

    Returns:
        Parsed event dict.

    Raises:
        asyncio.TimeoutError: If no event received within timeout.
    """
    try:
        message = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(message)
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError(f"No WebSocket event received within {timeout} seconds")


async def wait_for_event(
    ws,
    event_type: str,
    timeout: float = 5.0,
) -> dict:
    """Helper to wait for a specific event type.

    Args:
        ws: WebSocket connection.
        event_type: Expected event type (e.g., "session_started").
        timeout: Timeout in seconds.

    Returns:
        Parsed event dict.

    Raises:
        asyncio.TimeoutError: If event not received within timeout.
        ValueError: If received wrong event type.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            event = await receive_ws_event(ws, timeout=1.0)
            if event.get("event") == event_type:
                return event
        except asyncio.TimeoutError:
            continue

    raise asyncio.TimeoutError(f"Event '{event_type}' not received within {timeout} seconds")
