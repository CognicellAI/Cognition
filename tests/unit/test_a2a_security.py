"""Tests for deployment-level A2A Agent Card security discovery."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.agent.definition import A2AConfig, AgentDefinition
from server.app.protocols.a2a.card import build_agent_card_for_agent
from server.app.protocols.a2a.security import (
    A2ASecurityConfigurationError,
    parse_a2a_card_security,
)
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore

OAUTH2_SCHEMES: dict[str, dict[str, Any]] = {
    "oauth2": {
        "oauth2SecurityScheme": {
            "description": "KennelAMS machine credentials",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "https://auth.example.com/oauth/token",
                    "scopes": {
                        "a2a.invoke": "Invoke the agent",
                        "a2a.task.read": "Read owned tasks",
                    },
                }
            },
            "oauth2MetadataUrl": (
                "https://auth.example.com/.well-known/openid-configuration"
            ),
        }
    }
}
OAUTH2_REQUIREMENTS: list[dict[str, Any]] = [{"schemes": {"oauth2": {}}}]


def test_parse_security_and_publish_canonical_agent_card_fields() -> None:
    security = parse_a2a_card_security(OAUTH2_SCHEMES, OAUTH2_REQUIREMENTS)
    agent = AgentDefinition(
        name="private-runtime-name",
        display_name="Customer Support Concierge",
        system_prompt="Help customers.",
        a2a=A2AConfig(exposed=True),
    )

    card = build_agent_card_for_agent(
        agent,
        "https://agents.example.com",
        "0.12.0-rc.4",
        security=security,
    )
    payload = MessageToDict(card, preserving_proto_field_name=False)

    assert payload["securitySchemes"] == OAUTH2_SCHEMES
    assert payload["securityRequirements"] == OAUTH2_REQUIREMENTS


def test_empty_security_defaults_leave_card_unauthenticated() -> None:
    security = parse_a2a_card_security({}, [])
    agent = AgentDefinition(name="public-agent", system_prompt="Help customers.")

    card = build_agent_card_for_agent(
        agent,
        "https://agents.example.com",
        "0.12.0-rc.4",
        security=security,
    )
    payload = MessageToDict(card, preserving_proto_field_name=False)

    assert "securitySchemes" not in payload
    assert "securityRequirements" not in payload


def test_rejects_requirement_referencing_undeclared_scheme() -> None:
    with pytest.raises(A2ASecurityConfigurationError, match="undeclared schemes: oauth2"):
        parse_a2a_card_security({}, OAUTH2_REQUIREMENTS)


def test_rejects_unknown_protojson_fields() -> None:
    schemes: dict[str, dict[str, Any]] = {
        "oauth2": {"unknownSecurityScheme": {}}
    }

    with pytest.raises(A2ASecurityConfigurationError, match="oauth2.*invalid"):
        parse_a2a_card_security(schemes, [])


def test_rejects_scheme_without_variant() -> None:
    with pytest.raises(A2ASecurityConfigurationError, match="exactly one"):
        parse_a2a_card_security({"oauth2": {}}, [])


def test_rejects_oauth_flow_without_required_token_url() -> None:
    schemes: dict[str, dict[str, Any]] = {
        "oauth2": {
            "oauth2SecurityScheme": {
                "flows": {"clientCredentials": {"scopes": {}}}
            }
        }
    }

    with pytest.raises(A2ASecurityConfigurationError, match="oauth2.*invalid"):
        parse_a2a_card_security(schemes, [])


@pytest.mark.asyncio
async def test_mounted_card_uses_deployment_security_settings() -> None:
    from server.app.protocols.a2a.routes import mount_a2a_routes

    app = FastAPI()
    settings = MagicMock()
    settings.scope_keys = []
    settings.scoping_enabled = False
    settings.workspace_path = "/tmp"
    settings.a2a_security_schemes = OAUTH2_SCHEMES
    settings.a2a_security_requirements = OAUTH2_REQUIREMENTS
    config_store = DefaultConfigStore(MemoryConfigRegistry())
    await config_store.upsert_agent(
        "support-agent",
        {},
        {
            "name": "support-agent",
            "system_prompt": "Help customers.",
            "mode": "primary",
            "a2a": {"exposed": True},
        },
    )
    await mount_a2a_routes(
        app,
        settings=settings,
        config_store=config_store,
        session_agent_manager=MagicMock(),
        store=MagicMock(),
        version="0.12.0-rc.4",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://agents.example.com",
    ) as client:
        response = await client.get(
            "/a2a/support-agent/.well-known/agent-card.json"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["securitySchemes"] == OAUTH2_SCHEMES
    assert payload["securityRequirements"] == OAUTH2_REQUIREMENTS
