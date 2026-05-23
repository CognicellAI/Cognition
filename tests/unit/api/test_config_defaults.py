"""Unit tests for ConfigRegistry global defaults REST endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import set_config_store
from server.app.main import app
from server.app.storage.config_store import DefaultConfigStore

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    from server.app.storage.config_registry import MemoryConfigRegistry

    tmpdir = tmp_path_factory.mktemp("workspace")
    config_registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        workspace_path=tmpdir,
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
    assert response.json()["permissions"] == []


def test_patch_agent_defaults() -> None:
    response = client.patch(
        "/config/defaults/agent",
        json={
            "recursion_limit": 2000,
            "memory": ["AGENTS.md", "TEAM.md"],
            "interrupt_on": {
                "execute": {
                    "allowed_decisions": ["approve", "reject"],
                    "description": "Review shell commands",
                }
            },
            "permissions": [
                {
                    "operations": ["read"],
                    "paths": ["/workspace/repo/**"],
                    "mode": "allow",
                }
            ],
            "context_policy": {
                "max_input_tokens": 64000,
                "summarization_enabled": False,
                "retention": {"search": "retain"},
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["recursion_limit"] == 2000
    assert data["memory"] == ["AGENTS.md", "TEAM.md"]
    assert data["interrupt_on"]["execute"]["allowed_decisions"] == ["approve", "reject"]
    assert data["permissions"][0]["operations"] == ["read"]
    assert data["context_policy"]["max_input_tokens"] == 64000
    assert data["context_policy"]["summarization_enabled"] is False
    assert data["context_policy"]["retention"] == {"search": "retain"}
