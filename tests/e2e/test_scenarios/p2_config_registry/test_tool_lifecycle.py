"""Business Scenario: Tool Registration Lifecycle.

As a platform engineer, I want to register and remove custom tools via the API
so that agents can use them without redeploying the server.

Business Value:
- Dynamic tool registration without server restarts
- Persistent tool configuration across restarts
- Clean deregistration when tools are retired
"""

from __future__ import annotations

import uuid

import pytest


def _unique(prefix: str = "tool") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestToolLifecycle:
    """Tool registration CRUD end-to-end via the /tools API."""

    async def test_list_tools_returns_200(self, api_client) -> None:
        """GET /tools is accessible and returns a valid envelope."""
        response = await api_client.get("/tools")

        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert "count" in data
        assert isinstance(data["tools"], list)
        assert data["count"] == len(data["tools"])

    async def test_create_tool_succeeds(self, api_client) -> None:
        """POST /tools registers a tool and returns the registration."""
        name = _unique()

        create_resp = await api_client.post(
            "/tools",
            json={"name": name, "path": f"server.app.tools.{name}"},
        )
        assert create_resp.status_code == 201, create_resp.text

        data = create_resp.json()
        assert data["name"] == name
        assert data["source_type"] == "api_path"

        # Cleanup
        await api_client.delete(f"/tools/{name}")

    async def test_create_tool_is_idempotent(self, api_client) -> None:
        """POSTing the same tool name twice is a no-error upsert."""
        name = _unique()

        first = await api_client.post(
            "/tools",
            json={"name": name, "path": "module.v1"},
        )
        assert first.status_code == 201

        second = await api_client.post(
            "/tools",
            json={"name": name, "path": "module.v2"},
        )
        assert second.status_code == 201

        # Cleanup
        await api_client.delete(f"/tools/{name}")

    async def test_delete_tool_returns_204(self, api_client) -> None:
        """DELETE /tools/{name} removes the tool and returns 204."""
        name = _unique()

        await api_client.post(
            "/tools",
            json={"name": name, "path": "module.tool"},
        )

        delete_resp = await api_client.delete(f"/tools/{name}")
        assert delete_resp.status_code == 204

    async def test_delete_missing_tool_returns_404(self, api_client) -> None:
        """DELETE on an unregistered tool returns 404."""
        response = await api_client.delete("/tools/no-such-tool-xyz123")
        assert response.status_code == 404

    async def test_multiple_tools_coexist(self, api_client) -> None:
        """Multiple tools can be registered simultaneously."""
        names = [_unique() for _ in range(3)]

        for n in names:
            resp = await api_client.post(
                "/tools",
                json={"name": n, "path": f"module.{n}"},
            )
            assert resp.status_code == 201

        list_resp = await api_client.get("/tools")
        # GET /tools reads directly from ConfigStore.
        assert list_resp.status_code == 200

        # Cleanup
        for n in names:
            await api_client.delete(f"/tools/{n}")
