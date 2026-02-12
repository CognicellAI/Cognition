"""Async HTTP client for REST API endpoints."""

from typing import Any

import httpx

from client.tui.config import settings


class ApiClient:
    """Async HTTP client for Cognition server REST API.

    Handles connection pooling and error handling for all REST endpoints.
    """

    def __init__(self) -> None:
        """Initialize with base URL from settings."""
        self.client = httpx.AsyncClient(base_url=settings.base_url, timeout=30.0)

    async def close(self) -> None:
        """Close the HTTP client connection pool."""
        await self.client.aclose()

    async def health(self) -> dict[str, Any]:
        """Check server health status.

        Returns:
            Health check response with status, version, sessions count.

        Raises:
            httpx.ConnectError: If server is unreachable.
        """
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()

    async def list_projects(
        self,
        prefix: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List all projects with optional filters.

        Args:
            prefix: Filter by project name prefix.
            status: Filter by status (active, idle, resumable, pinned).

        Returns:
            Response with projects list and total count.
        """
        params: dict[str, str] = {}
        if prefix:
            params["prefix"] = prefix
        if status:
            params["status"] = status

        response = await self.client.get("/api/projects", params=params)
        response.raise_for_status()
        return response.json()

    async def get_project(self, project_id: str) -> dict[str, Any]:
        """Get detailed information about a project.

        Args:
            project_id: The project identifier.

        Returns:
            Project details including config, statistics, and sessions.
        """
        response = await self.client.get(f"/api/projects/{project_id}")
        response.raise_for_status()
        return response.json()

    async def create_project(
        self,
        user_prefix: str,
        network_mode: str = "OFF",
        repo_url: str | None = None,
        tags: list[str] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new project.

        Args:
            user_prefix: User-friendly name prefix.
            network_mode: Network access mode ("OFF" or "ON").
            repo_url: Optional git repository URL to clone.
            tags: Optional list of tags.
            description: Optional project description.

        Returns:
            Created project details with project_id and workspace_path.
        """
        data: dict[str, Any] = {
            "user_prefix": user_prefix,
            "network_mode": network_mode,
        }
        if repo_url:
            data["repo_url"] = repo_url
        if tags:
            data["tags"] = tags
        if description:
            data["description"] = description

        response = await self.client.post("/api/projects", json=data)
        response.raise_for_status()
        return response.json()

    async def delete_project(self, project_id: str, force: bool = False) -> dict[str, Any]:
        """Delete a project and its workspace.

        Args:
            project_id: The project identifier.
            force: Force delete even if sessions are active.

        Returns:
            Deletion confirmation.
        """
        params = {"force": "true"} if force else {}
        response = await self.client.delete(
            f"/api/projects/{project_id}",
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def create_session(
        self,
        project_id: str,
        network_mode: str | None = None,
    ) -> dict[str, Any]:
        """Create a new session for an existing project.

        Args:
            project_id: The project to resume.
            network_mode: Override network mode (uses project default if None).

        Returns:
            Session details with session_id and container_id.
        """
        data: dict[str, Any] = {}
        if network_mode:
            data["network_mode"] = network_mode

        response = await self.client.post(
            f"/api/projects/{project_id}/sessions",
            json=data,
        )
        response.raise_for_status()
        return response.json()

    async def list_resumable(self) -> dict[str, Any]:
        """List projects that can be resumed.

        Returns:
            List of resumable sessions with project info and last access times.
        """
        response = await self.client.get("/api/sessions/resumable")
        response.raise_for_status()
        return response.json()

    async def extend_project(
        self,
        project_id: str,
        days: int | None = None,
        pin: bool = False,
    ) -> dict[str, Any]:
        """Extend project lifetime or pin it.

        Args:
            project_id: The project identifier.
            days: Days to extend (optional).
            pin: Whether to pin the project (prevents auto-cleanup).

        Returns:
            Updated project status.
        """
        data: dict[str, Any] = {}
        if days is not None:
            data["days"] = days
        if pin:
            data["pin"] = True

        response = await self.client.post(
            f"/api/projects/{project_id}/extend",
            json=data,
        )
        response.raise_for_status()
        return response.json()


# Global API client instance
api_client: ApiClient | None = None


async def get_api_client() -> ApiClient:
    """Get or create the global API client instance."""
    global api_client
    if api_client is None:
        api_client = ApiClient()
    return api_client
