"""Builder-facing MCP OAuth handoff API tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.agent.definition import AgentDefinition
from server.app.agent.mcp_oauth_flow import McpOAuthFlowView
from server.app.api.dependencies import (
    get_config_store,
    get_mcp_oauth_flow_coordinator,
)
from server.app.api.routes.mcp_oauth import router


class _ConfigStore:
    async def get_agent_definition(self, name, scope=None):
        if name == "missing":
            return None
        return AgentDefinition.model_validate(
            {
                "name": name,
                "system_prompt": "Use direct MCP.",
                "mcp": {
                    "servers": {
                        "github": {
                            "url": "https://mcp.example.test/github",
                            "auth": {"type": "mcp_oauth"},
                        },
                        "public": {
                            "url": "https://mcp.example.test/public",
                            "auth": {"type": "none"},
                        },
                    }
                },
            }
        )


class _Coordinator:
    def __init__(self) -> None:
        self.completed = None

    async def begin(self, **kwargs):
        return McpOAuthFlowView(
            flow_id="flow-id",
            status="authorization_required",
            authorization_url="https://identity.example.test/authorize?state=opaque-state",
            expires_in_seconds=300,
        )

    async def complete(self, **kwargs):
        self.completed = kwargs
        return McpOAuthFlowView(flow_id="flow-id", status="authorized")

    def get(self, **kwargs):
        return McpOAuthFlowView(flow_id=kwargs["flow_id"], status="authorization_required")


def _client() -> tuple[TestClient, _Coordinator]:
    app = FastAPI()
    app.include_router(router)
    coordinator = _Coordinator()
    app.dependency_overrides[get_config_store] = lambda: _ConfigStore()
    app.dependency_overrides[get_mcp_oauth_flow_coordinator] = lambda: coordinator
    return TestClient(app), coordinator


def test_start_returns_only_credential_free_authorization_handoff() -> None:
    client, _ = _client()

    response = client.post("/mcp/oauth/agents/support/servers/github/authorizations")

    assert response.status_code == 200
    assert response.json() == {
        "flow_id": "flow-id",
        "status": "authorization_required",
        "authorization_url": "https://identity.example.test/authorize?state=opaque-state",
        "expires_in_seconds": 300,
        "failure_category": None,
    }
    assert "token" not in response.text.lower()


def test_callback_accepts_code_only_in_body_and_never_returns_it() -> None:
    client, coordinator = _client()

    response = client.post(
        "/mcp/oauth/callback",
        json={"code": "provider-authorization-code", "state": "opaque-state"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "authorized"
    assert "provider-authorization-code" not in response.text
    assert coordinator.completed == {
        "code": "provider-authorization-code",
        "state": "opaque-state",
        "effective_scope": {},
    }


def test_invalid_callback_is_generic_and_does_not_echo_secret_input() -> None:
    client, _ = _client()

    response = client.post(
        "/mcp/oauth/callback",
        json={"code": "provider-secret-code", "state": "opaque-state", "extra": True},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "authorization_callback_invalid"}
    assert "provider-secret-code" not in response.text


def test_start_rejects_non_oauth_or_missing_agent_server() -> None:
    client, _ = _client()

    assert (
        client.post("/mcp/oauth/agents/support/servers/public/authorizations").status_code
        == 404
    )
    assert (
        client.post("/mcp/oauth/agents/missing/servers/github/authorizations").status_code
        == 404
    )
