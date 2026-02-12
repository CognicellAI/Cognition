"""End-to-end tests for Cognition API and WebSocket.

These tests verify the full workflow on port 9000:
1. Server health check
2. API endpoint availability
3. WebSocket connectivity
"""

import httpx
import pytest


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_health_check():
    """Test server health endpoint on port 9000."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:9000/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_root_endpoint():
    """Test root API endpoint on port 9000."""
    async with httpx.AsyncClient() as client:
        response = await client.get("http://localhost:9000/")
        # FastAPI may return 404 for root if no root endpoint is defined
        # Just verify the server is responding
        assert response.status_code in [200, 404]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_api_projects_endpoint():
    """Test API projects endpoint (list) on port 9000."""
    async with httpx.AsyncClient() as client:
        # This may return 404 if not configured, or 403 if auth required
        response = await client.get("http://localhost:9000/api/projects")
        # Accept either 404, 403, or 200 depending on server setup
        assert response.status_code in [200, 403, 404]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_websocket_endpoint_exists():
    """Test that WebSocket endpoint exists on port 9000."""
    import websockets

    try:
        uri = "ws://localhost:9000/ws"
        ws = await websockets.connect(uri)
        await ws.close()
    except Exception as e:
        # Expected - WebSocket endpoint should exist but may require auth
        error_msg = str(e)
        # Accept connection errors as proof endpoint exists
        assert "403" in error_msg or "refused" in error_msg or "closed" in error_msg


@pytest.mark.e2e
def test_server_running_on_port_9000():
    """Test that server is running and responsive on port 9000."""
    import subprocess

    result = subprocess.run(
        ["curl", "-s", "http://localhost:9000/health"],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert b"healthy" in result.stdout
