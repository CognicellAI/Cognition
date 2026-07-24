"""E2E test configuration and shared fixtures."""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest_asyncio

E2E_DEFAULT_AGENT_NAME = "default"
E2E_READONLY_AGENT_NAME = "readonly"
E2E_PROVIDER_ID = "e2e-mock"


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture
async def server():
    """Start the Cognition server for E2E tests.

    If COGNITION_E2E_URL is set, use that server directly (e.g. a
    docker-compose instance) instead of starting a local uvicorn process.

    Otherwise, spins up a uvicorn process on a random free port, waits
    for /ready, yields the base URL, then tears down.
    """
    existing = os.environ.get("COGNITION_E2E_URL")
    if existing:
        yield existing.rstrip("/")
        return

    with tempfile.TemporaryDirectory(prefix="cognition-e2e-") as tmpdir:
        workspace = Path(tmpdir)
        agents_dir = workspace / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / f"{E2E_DEFAULT_AGENT_NAME}.yaml").write_text(
            "name: default\n"
            "description: Explicitly provisioned E2E test agent.\n"
            "system_prompt: You are a helpful E2E test agent.\n"
            "mode: primary\n",
            encoding="utf-8",
        )
        (agents_dir / f"{E2E_READONLY_AGENT_NAME}.yaml").write_text(
            "name: readonly\n"
            "description: Explicitly provisioned read-only E2E test agent.\n"
            "system_prompt: You are a careful read-only E2E test agent.\n"
            "mode: primary\n",
            encoding="utf-8",
        )

        port = _find_free_port()
        metrics_port = _find_free_port()

        env = os.environ.copy()
        env["COGNITION_PORT"] = str(port)
        env["COGNITION_HOST"] = "127.0.0.1"
        env["COGNITION_WORKSPACE_ROOT"] = str(workspace)
        env["COGNITION_LLM_PROVIDER"] = "mock"
        env["COGNITION_METRICS_PORT"] = str(metrics_port)
        # Existing E2E tests exercise local/mock execution. Production defaults
        # remain strict; the test deployment opts into standalone development mode.
        env["COGNITION_ALLOW_UNSAFE_LOCAL_EXECUTION"] = "true"
        env["COGNITION_ALLOW_HOST_TOOLS"] = "true"
        env["COGNITION_ALLOW_API_PYTHON_TOOLS"] = "true"
        # Disable MLflow to avoid side effects in tests
        env["COGNITION_MLFLOW_ENABLED"] = "false"
        # Disable OpenTelemetry to avoid port conflicts
        env["COGNITION_OTEL_ENABLED"] = "false"

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
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent),
        )

        base_url = f"http://127.0.0.1:{port}"
        start_time = time.time()
        timeout = 15  # seconds

        last_error = None
        while time.time() - start_time < timeout:
            # Check if process exited early
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    f"Server exited early. stdout: {stdout.decode()[:500]}, "
                    f"stderr: {stderr.decode()[:500]}"
                )

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{base_url}/ready", timeout=2.0)
                    if response.status_code == 200:
                        break
            except Exception as e:
                last_error = str(e)
                await asyncio.sleep(0.2)
        else:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise RuntimeError(
                f"Server failed to start within {timeout}s. "
                f"Last error: {last_error}. "
                f"stdout: {stdout.decode()[-500:]}, "
                f"stderr: {stderr.decode()[-500:]}"
            )

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await ensure_e2e_provider(client, base_url)

        yield base_url

        # Cleanup
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@pytest_asyncio.fixture
async def scope_headers(server: str) -> dict[str, str]:
    """Detect if session scoping is enabled and return appropriate headers.

    Probes the server's /config endpoint to determine whether scoping
    is active. Returns ``{"X-Cognition-Scope-User": "test-user"}`` when
    scoping is enabled, ``{}`` otherwise.

    This allows both API-level and scenario tests to work against
    deployments with and without scoping without per-test changes.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        try:
            resp = await client.get(f"{server}/config")
            if resp.status_code == 200:
                config = resp.json()
                if config.get("server", {}).get("scoping_enabled", False):
                    return {"X-Cognition-Scope-User": "test-user"}
        except Exception:
            pass
    return {}


async def ensure_e2e_agent(
    client: httpx.AsyncClient,
    base_url: str,
    name: str = E2E_DEFAULT_AGENT_NAME,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    """Ensure the shared E2E Agent exists for this deployment/scope."""
    response = await client.post(
        f"{base_url}/agents",
        json={
            "name": name,
            "description": f"Explicitly provisioned {name} E2E Agent.",
            "system_prompt": f"You are the {name} E2E test agent.",
            "mode": "primary",
        },
        headers=headers or {},
    )
    if response.status_code in {200, 201, 409}:
        return
    existing = await client.get(f"{base_url}/agents/{name}", headers=headers or {})
    if existing.status_code == 200:
        return
    raise AssertionError(
        f"Failed to provision E2E agent {name!r}: {response.status_code} {response.text}"
    )


async def ensure_e2e_provider(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    """Ensure the shared E2E mock provider exists for this deployment/scope."""
    response = await client.post(
        f"{base_url}/models/providers",
        json={
            "id": E2E_PROVIDER_ID,
            "provider": "mock",
            "model": "mock-model",
            "display_name": "E2E Mock Provider",
            "enabled": True,
            "priority": 0,
        },
        headers=headers or {},
    )
    if response.status_code in {200, 201, 409}:
        return
    existing = await client.get(
        f"{base_url}/models/providers",
        headers=headers or {},
    )
    if existing.status_code == 200 and any(
        p.get("id") == E2E_PROVIDER_ID for p in existing.json().get("providers", [])
    ):
        return
    raise AssertionError(
        f"Failed to provision E2E provider {E2E_PROVIDER_ID!r}: "
        f"{response.status_code} {response.text}"
    )


@pytest_asyncio.fixture
async def e2e_agent_name(server: str, scope_headers: dict[str, str]) -> str:
    """Builder-provisioned Agent name used by v0.13 E2E tests."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        await ensure_e2e_provider(client, server, headers=scope_headers)
        await ensure_e2e_agent(client, server, E2E_DEFAULT_AGENT_NAME, headers=scope_headers)
    return E2E_DEFAULT_AGENT_NAME
