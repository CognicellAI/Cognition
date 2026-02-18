"""E2E test configuration and shared fixtures."""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def server(unused_tcp_port):
    """Start the Cognition server for E2E tests.

    This is a shared fixture used across all E2E test classes.
    It ensures each test gets a unique port for both the API and metrics.
    """
    port = unused_tcp_port
    metrics_port = unused_tcp_port + 1000  # Offset to avoid conflicts

    env = os.environ.copy()
    env["COGNITION_PORT"] = str(port)
    env["COGNITION_HOST"] = "127.0.0.1"
    env["COGNITION_LLM_PROVIDER"] = "mock"
    env["COGNITION_METRICS_PORT"] = str(metrics_port)

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(Path(__file__).parent.parent),
    )

    base_url = f"http://127.0.0.1:{port}"
    start_time = time.time()
    timeout = 15  # 15 seconds to start

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
                response = await client.get(f"{base_url}/ready")
                if response.status_code == 200:
                    break
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(0.1)
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
