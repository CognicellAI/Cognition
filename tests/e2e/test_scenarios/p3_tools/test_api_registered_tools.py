"""E2E Scenarios: ConfigStore tools bridge (Phase 3b — #23).

As a builder running Cognition in a container separate from my application,
I want to register custom Python tools via the REST API — either as source code
or module paths — and have them available to agents immediately,
so that I can extend agent capabilities dynamically without SSH access,
container rebuilds, or server restarts.

Business Value:
- Tools registered via POST /tools (with code or path) are persisted in the DB
- GET /tools returns tools from ConfigStore with source_type discrimination
- Tool validation rejects invalid payloads (neither code nor path; both)
- Disabled tools appear in the listing but are skipped at runtime
- Full CRUD lifecycle: register → verify → get by name → delete → gone

Run against: docker-compose environment at http://localhost:8000
"""

from __future__ import annotations

import textwrap
import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


def _unique(prefix: str = "tool") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _simple_tool_code(tool_name: str) -> str:
    """Return Python source for a simple @tool-decorated function."""
    # Replace hyphens with underscores for a valid Python identifier
    fn_name = tool_name.replace("-", "_")
    return textwrap.dedent(f"""\
        from langchain_core.tools import tool

        @tool
        def {fn_name}(query: str) -> str:
            \"\"\"A test tool registered via the API.\"\"\"
            return f"Result for: {{query}}"
    """)


@pytest.mark.asyncio
@pytest.mark.e2e
class TestToolRegistrationLifecycle:
    """Full CRUD lifecycle for API-registered tools."""

    async def test_list_tools_returns_200(self, api_client: ScenarioTestClient) -> None:
        """GET /tools is reachable and returns the expected envelope."""
        response = await api_client.get("/tools")
        assert response.status_code == 200

        data = response.json()
        assert "tools" in data
        assert "count" in data
        assert isinstance(data["tools"], list)
        assert data["count"] == len(data["tools"])

    async def test_register_tool_with_code_returns_201(
        self, api_client: ScenarioTestClient
    ) -> None:
        """POST /tools with source code registers successfully."""
        name = _unique()
        code = _simple_tool_code(name)

        response = await api_client.post("/tools", json={"name": name, "code": code})
        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )

        data = response.json()
        assert data["name"] == name
        assert data["source_type"] == "api_code"

        # Cleanup
        await api_client.delete(f"/tools/{name}")

    async def test_register_tool_with_path_returns_201(
        self, api_client: ScenarioTestClient
    ) -> None:
        """POST /tools with a module path registers successfully."""
        name = _unique()

        response = await api_client.post(
            "/tools", json={"name": name, "path": f"server.app.tools.{name}"}
        )
        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )

        data = response.json()
        assert data["name"] == name
        assert data["source_type"] == "api_path"

        # Cleanup
        await api_client.delete(f"/tools/{name}")

    async def test_delete_tool_returns_204(self, api_client: ScenarioTestClient) -> None:
        """DELETE /tools/{name} removes the tool and returns 204."""
        name = _unique()

        # Register
        create_resp = await api_client.post("/tools", json={"name": name, "path": "a.b.c"})
        assert create_resp.status_code == 201

        # Delete
        delete_resp = await api_client.delete(f"/tools/{name}")
        assert delete_resp.status_code == 204

    async def test_delete_nonexistent_tool_returns_404(
        self, api_client: ScenarioTestClient
    ) -> None:
        """DELETE /tools/{name} for a non-existent tool returns 404."""
        response = await api_client.delete(f"/tools/does-not-exist-{uuid.uuid4().hex}")
        assert response.status_code == 404

    async def test_upsert_on_duplicate_name(self, api_client: ScenarioTestClient) -> None:
        """POSTing the same tool name twice is a no-error upsert."""
        name = _unique()

        first = await api_client.post("/tools", json={"name": name, "path": "module.v1"})
        assert first.status_code == 201

        second = await api_client.post("/tools", json={"name": name, "path": "module.v2"})
        assert second.status_code == 201

        # Cleanup
        await api_client.delete(f"/tools/{name}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestToolValidation:
    """POST /tools validates the payload — XOR between code and path."""

    async def test_neither_code_nor_path_returns_422(self, api_client: ScenarioTestClient) -> None:
        """POST /tools with only name (no code, no path) returns 422."""
        response = await api_client.post("/tools", json={"name": _unique()})
        assert response.status_code == 422, (
            f"Expected 422, got {response.status_code}: {response.text}"
        )

    async def test_both_code_and_path_returns_422(self, api_client: ScenarioTestClient) -> None:
        """POST /tools with both code and path returns 422."""
        name = _unique()
        response = await api_client.post(
            "/tools",
            json={
                "name": name,
                "code": "pass",
                "path": "some.module",
            },
        )
        assert response.status_code == 422, (
            f"Expected 422, got {response.status_code}: {response.text}"
        )

    async def test_missing_name_returns_422(self, api_client: ScenarioTestClient) -> None:
        """POST /tools without a name returns 422."""
        response = await api_client.post("/tools", json={"path": "some.module"})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.e2e
class TestUnifiedToolListing:
    """GET /tools returns tools from ConfigStore."""

    async def test_api_registered_tool_appears_in_listing(
        self, api_client: ScenarioTestClient
    ) -> None:
        """A tool registered via POST /tools appears in GET /tools."""
        name = _unique()

        await api_client.post("/tools", json={"name": name, "path": "some.module"})

        try:
            response = await api_client.get("/tools")
            assert response.status_code == 200

            tools = response.json()["tools"]
            names = [t["name"] for t in tools]
            assert name in names, (
                f"Registered tool '{name}' not found in GET /tools response: {names}"
            )
        finally:
            await api_client.delete(f"/tools/{name}")

    async def test_api_tool_has_correct_source_type(self, api_client: ScenarioTestClient) -> None:
        """API-registered tools have source_type='api_code' or 'api_path'."""
        path_tool = _unique("path")
        code_tool = _unique("code")

        await api_client.post("/tools", json={"name": path_tool, "path": "x.y.z"})
        await api_client.post("/tools", json={"name": code_tool, "code": "pass"})

        try:
            response = await api_client.get("/tools")
            tools = {t["name"]: t for t in response.json()["tools"]}

            assert path_tool in tools, f"Path tool '{path_tool}' not in listing"
            assert tools[path_tool]["source_type"] == "api_path", (
                f"Expected api_path, got: {tools[path_tool]['source_type']}"
            )

            assert code_tool in tools, f"Code tool '{code_tool}' not in listing"
            assert tools[code_tool]["source_type"] == "api_code", (
                f"Expected api_code, got: {tools[code_tool]['source_type']}"
            )
        finally:
            await api_client.delete(f"/tools/{path_tool}")
            await api_client.delete(f"/tools/{code_tool}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestGetToolByName:
    """GET /tools/{name} returns a single tool by name."""

    async def test_get_registered_tool_by_name(self, api_client: ScenarioTestClient) -> None:
        """GET /tools/{name} returns the tool registered under that name."""
        name = _unique()

        await api_client.post(
            "/tools", json={"name": name, "path": "a.b.c", "description": "E2E test tool"}
        )

        try:
            response = await api_client.get(f"/tools/{name}")
            assert response.status_code == 200

            data = response.json()
            assert data["name"] == name
            assert data["source_type"] in ("api_path", "api_code", "file")
        finally:
            await api_client.delete(f"/tools/{name}")

    async def test_get_nonexistent_tool_returns_404(self, api_client: ScenarioTestClient) -> None:
        """GET /tools/{name} for an unknown tool returns 404."""
        response = await api_client.get(f"/tools/no-such-tool-{uuid.uuid4().hex}")
        assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.e2e
class TestDisabledTools:
    """Disabled tools appear in the listing but are marked as disabled."""

    async def test_disabled_tool_visible_in_listing(self, api_client: ScenarioTestClient) -> None:
        """A tool registered with enabled=false appears in GET /tools with enabled=false."""
        name = _unique("disabled")

        await api_client.post(
            "/tools",
            json={"name": name, "path": "some.module", "enabled": False},
        )

        try:
            response = await api_client.get("/tools")
            assert response.status_code == 200

            tools = {t["name"]: t for t in response.json()["tools"]}
            assert name in tools, f"Disabled tool '{name}' not found in listing"
            assert tools[name].get("enabled") is False, (
                f"Expected enabled=False, got: {tools[name]}"
            )
        finally:
            await api_client.delete(f"/tools/{name}")
