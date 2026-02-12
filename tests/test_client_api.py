"""Unit tests for client API module."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from client.tui.api import ApiClient, get_api_client


@pytest.mark.unit
class TestApiClient:
    """Test ApiClient class."""

    @pytest.fixture
    def client(self):
        """Create an ApiClient instance for testing."""
        return ApiClient()

    @pytest.mark.asyncio
    async def test_client_initialization(self, client):
        """Test ApiClient initializes with correct base URL."""
        assert client.client is not None
        # Base URL should be constructed from settings
        assert "localhost:8000" in str(client.client.base_url)

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        """Test health check endpoint call."""
        mock_response = {
            "status": "healthy",
            "version": "0.1.0",
            "sessions_active": 0,
            "llm": {"configured": True, "provider": "anthropic"},
        }

        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value=mock_response)
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp

            result = await client.health()

            assert result["status"] == "healthy"
            assert result["version"] == "0.1.0"
            mock_get.assert_called_once_with("/health")

    @pytest.mark.asyncio
    async def test_list_projects(self, client):
        """Test listing projects."""
        mock_response = {
            "projects": [
                {
                    "project_id": "test-abc123",
                    "user_prefix": "test",
                    "last_accessed": "2026-02-12T00:00:00Z",
                    "total_sessions": 2,
                    "status": "idle",
                    "cleanup_in_days": 28,
                }
            ],
            "total": 1,
        }

        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value=mock_response)
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp

            result = await client.list_projects(prefix="test")

            assert len(result["projects"]) == 1
            assert result["projects"][0]["project_id"] == "test-abc123"
            mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_project(self, client):
        """Test creating a project."""
        mock_response = {
            "project_id": "my-project-xyz789",
            "user_prefix": "my-project",
            "created_at": "2026-02-12T00:00:00Z",
            "workspace_path": "/workspaces/my-project-xyz789/repo",
        }

        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value=mock_response)
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client.client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp

            result = await client.create_project(
                user_prefix="my-project",
                network_mode="OFF",
            )

            assert result["project_id"] == "my-project-xyz789"
            assert result["user_prefix"] == "my-project"
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_project(self, client):
        """Test getting project details."""
        mock_response = {
            "project_id": "test-abc123",
            "user_prefix": "test",
            "config": {"network_mode": "OFF"},
            "statistics": {
                "total_sessions": 2,
                "total_messages": 42,
                "files_modified": 5,
            },
        }

        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value=mock_response)
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp

            result = await client.get_project("test-abc123")

            assert result["project_id"] == "test-abc123"
            assert result["statistics"]["total_messages"] == 42
            mock_get.assert_called_once_with("/api/projects/test-abc123")

    @pytest.mark.asyncio
    async def test_list_resumable(self, client):
        """Test listing resumable projects."""
        mock_response = {
            "sessions": [
                {
                    "project_id": "test-abc123",
                    "user_prefix": "test",
                    "last_accessed": "2026-02-12T00:00:00Z",
                    "total_messages": 42,
                }
            ],
            "total": 1,
        }

        mock_resp = MagicMock()
        mock_resp.json = MagicMock(return_value=mock_response)
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp

            result = await client.list_resumable()

            assert len(result["sessions"]) == 1
            assert result["sessions"][0]["project_id"] == "test-abc123"

    @pytest.mark.asyncio
    async def test_client_close(self):
        """Test closing the client."""
        client = ApiClient()

        with patch.object(client.client, "aclose", new_callable=AsyncMock) as mock_close:
            await client.close()
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_api_client_singleton(self):
        """Test that get_api_client returns singleton instance."""
        # Clear the module-level instance if it exists
        import client.tui.api as api_module

        api_module.api_client = None

        client1 = await get_api_client()
        client2 = await get_api_client()

        assert client1 is client2


@pytest.mark.unit
class TestApiClientErrors:
    """Test API client error handling."""

    @pytest.fixture
    def client(self):
        """Create an ApiClient instance for testing."""
        return ApiClient()

    @pytest.mark.asyncio
    async def test_health_connection_error(self, client):
        """Test handling of connection errors."""
        with patch.object(
            client.client, "get", side_effect=httpx.ConnectError("Connection failed")
        ):
            with pytest.raises(httpx.ConnectError):
                await client.health()

    @pytest.mark.asyncio
    async def test_list_projects_http_error(self, client):
        """Test handling of HTTP errors."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )

        with patch.object(client.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp

            with pytest.raises(httpx.HTTPStatusError):
                await client.list_projects()
