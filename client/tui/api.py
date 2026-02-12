"""REST API client with SSE support for the TUI.

Replaces WebSocket with HTTP REST API and Server-Sent Events.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator, Callable

import httpx


class ApiClient:
    """HTTP client for Cognition REST API with SSE support."""

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
        """Check server health."""
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Project Endpoints
    # ========================================================================

    async def create_project(
        self,
        name: str,
        description: str | None = None,
        path: str | None = None,
    ) -> dict:
        """Create a new project.

        Args:
            name: Project name
            description: Optional project description
            path: Optional custom path for project workspace

        Returns:
            Project creation response with id, name, path, etc.
        """
        data = {"name": name}
        if description:
            data["description"] = description
        if path:
            data["path"] = path

        response = await self.client.post("/projects", json=data)
        response.raise_for_status()
        return response.json()

    async def list_projects(self) -> dict:
        """List all projects.

        Returns:
            List of projects with total count.
        """
        response = await self.client.get("/projects")
        response.raise_for_status()
        return response.json()

    async def get_project(self, project_id: str) -> dict:
        """Get project details.

        Args:
            project_id: Project ID

        Returns:
            Project details.
        """
        response = await self.client.get(f"/projects/{project_id}")
        response.raise_for_status()
        return response.json()

    async def delete_project(self, project_id: str) -> None:
        """Delete a project.

        Args:
            project_id: Project ID to delete
        """
        response = await self.client.delete(f"/projects/{project_id}")
        response.raise_for_status()

    # ========================================================================
    # Session Endpoints
    # ========================================================================

    async def create_session(
        self,
        project_id: str,
        title: str | None = None,
        config: dict | None = None,
    ) -> dict:
        """Create a new session.

        Args:
            project_id: ID of the project for this session
            title: Optional session title
            config: Optional session configuration

        Returns:
            Session creation response with id, thread_id, config, etc.
        """
        data = {"project_id": project_id}
        if title:
            data["title"] = title
        if config:
            data["config"] = config

        response = await self.client.post("/sessions", json=data)
        response.raise_for_status()
        return response.json()

    async def list_sessions(self, project_id: str | None = None) -> dict:
        """List all sessions.

        Args:
            project_id: Optional filter by project

        Returns:
            List of sessions with total count.
        """
        params = {}
        if project_id:
            params["project_id"] = project_id

        response = await self.client.get("/sessions", params=params)
        response.raise_for_status()
        return response.json()

    async def get_session(self, session_id: str) -> dict:
        """Get session details.

        Args:
            session_id: Session ID

        Returns:
            Session details.
        """
        response = await self.client.get(f"/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    async def update_session(
        self,
        session_id: str,
        title: str | None = None,
        config: dict | None = None,
    ) -> dict:
        """Update a session.

        Args:
            session_id: Session ID to update
            title: Optional new title
            config: Optional new configuration

        Returns:
            Updated session details.
        """
        data = {}
        if title:
            data["title"] = title
        if config:
            data["config"] = config

        response = await self.client.patch(f"/sessions/{session_id}", json=data)
        response.raise_for_status()
        return response.json()

    async def delete_session(self, session_id: str) -> None:
        """Delete a session.

        Args:
            session_id: Session ID to delete
        """
        response = await self.client.delete(f"/sessions/{session_id}")
        response.raise_for_status()

    async def abort_session(self, session_id: str) -> dict:
        """Abort current operation in a session.

        Args:
            session_id: Session ID

        Returns:
            Abort response.
        """
        response = await self.client.post(f"/sessions/{session_id}/abort")
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Message Endpoints with SSE
    # ========================================================================

    async def send_message(
        self,
        session_id: str,
        content: str,
        parent_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Send a message and stream the response via SSE.

        Args:
            session_id: Session ID
            content: Message content
            parent_id: Optional parent message ID for threading

        Yields:
            SSE events: token, tool_call, tool_result, error, done, usage
        """
        data = {"content": content}
        if parent_id:
            data["parent_id"] = parent_id

        async with self.client.stream(
            "POST",
            f"/sessions/{session_id}/messages",
            json=data,
            headers={"Accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    yield {"event": event_type, "data": data}

    async def list_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List messages in a session.

        Args:
            session_id: Session ID
            limit: Maximum number of messages to return
            offset: Offset for pagination

        Returns:
            List of messages with total count.
        """
        params = {"limit": limit, "offset": offset}
        response = await self.client.get(
            f"/sessions/{session_id}/messages",
            params=params,
        )
        response.raise_for_status()
        return response.json()

    # ========================================================================
    # Config Endpoints
    # ========================================================================

    async def get_config(self) -> dict:
        """Get server configuration.

        Returns:
            Server configuration (sanitized, no secrets).
        """
        response = await self.client.get("/config")
        response.raise_for_status()
        return response.json()


class EventHandler:
    """Helper class for handling SSE events in the TUI."""

    def __init__(self):
        self.tokens: list[str] = []
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.error: str | None = None
        self.usage: dict | None = None
        self.done = False

    def handle_event(self, event: dict) -> None:
        """Handle a single SSE event.

        Args:
            event: Event dict with 'event' and 'data' keys
        """
        event_type = event.get("event")
        data = event.get("data", {})

        if event_type == "token":
            self.tokens.append(data.get("content", ""))
        elif event_type == "tool_call":
            self.tool_calls.append(data)
        elif event_type == "tool_result":
            self.tool_results.append(data)
        elif event_type == "error":
            self.error = data.get("message", "Unknown error")
        elif event_type == "usage":
            self.usage = data
        elif event_type == "done":
            self.done = True

    def get_full_response(self) -> str:
        """Get the full text response from tokens."""
        return "".join(self.tokens)

    def has_tool_calls(self) -> bool:
        """Check if any tool calls were made."""
        return len(self.tool_calls) > 0

    def clear(self) -> None:
        """Clear all accumulated data."""
        self.tokens.clear()
        self.tool_calls.clear()
        self.tool_results.clear()
        self.error = None
        self.usage = None
        self.done = False
