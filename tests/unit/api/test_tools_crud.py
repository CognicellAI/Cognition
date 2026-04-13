"""Unit tests for /tools API endpoints (CRUD)."""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import set_config_store
from server.app.main import app
from server.app.storage.config_store import DefaultConfigStore
from server.app.storage.sqlite import SqliteStorageBackend

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    tmpdir = tmp_path_factory.mktemp("workspace")
    from server.app.storage.config_registry import MemoryConfigRegistry

    with tempfile.TemporaryDirectory() as db_tmpdir:
        storage = SqliteStorageBackend(
            connection_string=f"{db_tmpdir}/test.db",
            workspace_path=db_tmpdir,
        )
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(storage.initialize())

        config_registry = MemoryConfigRegistry()
        config_store = DefaultConfigStore(
            config_registry=config_registry,
            workspace_path=tmpdir,
        )
        set_config_store(config_store)
        yield

        loop.run_until_complete(storage.close())
        loop.close()


class TestListTools:
    def test_list_returns_200(self):
        response = client.get("/tools")
        assert response.status_code == 200

    def test_list_returns_empty_when_no_tools_registered(self):
        response = client.get("/tools")
        assert response.status_code == 200
        data = response.json()
        assert "tools" in data
        assert "count" in data


class TestCreateTool:
    def test_create_tool_succeeds(self):
        payload = {
            "name": "test-tool-create",
            "path": "server.app.tools.test_tool",
            "enabled": True,
            "interrupt_on": True,
        }
        response = client.post("/tools", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-tool-create"
        assert data["interrupt_on"] is True

    def test_create_tool_missing_name_returns_422(self):
        response = client.post("/tools", json={"path": "some.path"})
        assert response.status_code == 422

    def test_create_tool_missing_both_path_and_code_returns_422(self):
        """POST /tools with neither path nor code returns 422."""
        response = client.post("/tools", json={"name": "no-source"})
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


class TestUpdateTool:
    def test_patch_tool_updates_interrupt_on(self):
        client.post(
            "/tools", json={"name": "test-tool-patch", "path": "a.b", "interrupt_on": False}
        )
        response = client.patch("/tools/test-tool-patch", json={"interrupt_on": True})
        assert response.status_code == 200
        assert response.json()["interrupt_on"] is True
