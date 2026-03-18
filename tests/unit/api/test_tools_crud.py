"""Unit tests for /tools API endpoints (CRUD)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.agent.agent_definition_registry import initialize_agent_definition_registry
from server.app.main import app

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    tmpdir = tmp_path_factory.mktemp("workspace")
    from pathlib import Path

    from server.app.storage.config_registry import MemoryConfigRegistry, set_config_registry

    initialize_agent_definition_registry(Path(tmpdir))
    set_config_registry(MemoryConfigRegistry())
    yield


class TestListTools:
    def test_list_returns_200(self):
        response = client.get("/tools")
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data

    def test_list_returns_list_type(self):
        response = client.get("/tools")
        assert isinstance(response.json()["tools"], list)


class TestCreateTool:
    def test_create_tool_succeeds(self):
        payload = {
            "name": "test-tool-create",
            "path": "server.app.tools.test_tool",
            "enabled": True,
        }
        response = client.post("/tools", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-tool-create"

    def test_create_tool_missing_name_returns_422(self):
        response = client.post("/tools", json={"path": "some.path"})
        assert response.status_code == 422

    def test_create_tool_missing_path_returns_422(self):
        response = client.post("/tools", json={"name": "no-path"})
        assert response.status_code == 422

    def test_create_tool_upserts_on_duplicate(self):
        payload = {"name": "test-tool-dup", "path": "a.b.c"}
        client.post("/tools", json=payload)
        payload["path"] = "x.y.z"
        response = client.post("/tools", json=payload)
        assert response.status_code == 201


class TestDeleteTool:
    def test_delete_existing_tool(self):
        client.post("/tools", json={"name": "test-tool-delete", "path": "a.b"})
        response = client.delete("/tools/test-tool-delete")
        assert response.status_code == 204

    def test_delete_missing_tool_returns_404(self):
        response = client.delete("/tools/nonexistent-tool-xyz")
        assert response.status_code == 404


class TestReloadTools:
    def test_reload_endpoint_returns_503_when_not_initialized(self):
        """POST /tools/reload returns 503 when the tool registry hasn't been initialized."""
        response = client.post("/tools/reload")
        # The agent_registry (tool discovery layer) is not initialized in unit tests
        assert response.status_code == 503

    def test_errors_endpoint_returns_200(self):
        """GET /tools/errors should return any load errors."""
        response = client.get("/tools/errors")
        assert response.status_code == 200
