"""E2E test configuration and shared fixtures."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture
async def server():
    """Start the Cognition server for E2E tests.

    Spins up a uvicorn process on a random free port, waits for /ready,
    yields the base URL, then tears down.
    """
    port = _find_free_port()
    metrics_port = _find_free_port()

    env = os.environ.copy()
    env["COGNITION_PORT"] = str(port)
    env["COGNITION_HOST"] = "127.0.0.1"
    env["COGNITION_LLM_PROVIDER"] = "mock"
    env["COGNITION_METRICS_PORT"] = str(metrics_port)
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

    yield base_url

    # Cleanup
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
