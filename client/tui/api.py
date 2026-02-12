"""REST API client for the TUI."""

from __future__ import annotations

import httpx


class ApiClient:
    """HTTP client for Cognition REST API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        """Initialize the API client.

        Args:
            base_url: The base URL of the Cognition server.
        """
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def health(self) -> dict:
        """Check server health.

        Returns:
            Health check response.
        """
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()

    async def create_project(
        self,
        user_prefix: str | None = None,
        project_path: str | None = None,
    ) -> dict:
        """Create a new project.

        Args:
            user_prefix: Optional user-friendly prefix for project ID.
            project_path: Optional custom path for the project.

        Returns:
            Project creation response with project_id and project_path.
        """
        data = {}
        if user_prefix:
            data["user_prefix"] = user_prefix
        if project_path:
            data["project_path"] = project_path

        response = await self.client.post("/projects", json=data)
        response.raise_for_status()
        return response.json()
