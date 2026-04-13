"""Unit tests for ConfigRegistry global defaults REST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.agent.agent_definition_registry import initialize_agent_definition_registry
from server.app.api.dependencies import set_config_store
from server.app.main import app
from server.app.storage.config_store import DefaultConfigStore

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    from pathlib import Path

    from server.app.storage.config_registry import MemoryConfigRegistry

    tmpdir = tmp_path_factory.mktemp("workspace")
    def_registry = initialize_agent_definition_registry(Path(tmpdir))
    config_registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        agent_definition_registry=def_registry,
    )
    set_config_store(config_store)
    yield


def test_get_provider_defaults() -> None:
    response = client.get("/config/defaults/provider")

    assert response.status_code == 200
    assert response.json()["provider"] == "openai_compatible"


def test_patch_provider_defaults() -> None:
    response = client.patch(
        "/config/defaults/provider",
        json={"max_tokens": 32000, "model": "gpt-4.1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["max_tokens"] == 32000
    assert data["model"] == "gpt-4.1"


def test_get_agent_defaults() -> None:
    response = client.get("/config/defaults/agent")

    assert response.status_code == 200
    assert response.json()["recursion_limit"] == 1000


def test_patch_agent_defaults() -> None:
    response = client.patch(
        "/config/defaults/agent",
        json={"recursion_limit": 2000, "memory": ["AGENTS.md", "TEAM.md"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recursion_limit"] == 2000
    assert data["memory"] == ["AGENTS.md", "TEAM.md"]
