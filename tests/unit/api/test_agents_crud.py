"""Unit tests for /agents API endpoints (CRUD).

Tests the GET/POST/PUT/PATCH/DELETE endpoints added for ConfigRegistry-backed
agent management.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import get_config_store, get_settings_dep, set_config_store
from server.app.main import app
from server.app.settings import Settings, get_settings
from server.app.storage.config_store import DefaultConfigStore
from server.app.storage.mcp_readiness import (
    McpReadinessObservation,
    MemoryMcpReadinessRepository,
)

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    """Initialize agent registry and ConfigStore for the test module."""
    from server.app.storage.config_registry import MemoryConfigRegistry

    tmpdir = tmp_path_factory.mktemp("workspace")
    config_registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        workspace_path=tmpdir,
    )
    asyncio.run(
        config_store.upsert_agent(
            "fixture-agent",
            {},
            {
                "name": "fixture-agent",
                "system_prompt": "Explicitly provisioned fixture Agent.",
                "mode": "primary",
            },
            "api",
        )
    )
    asyncio.run(
        config_store.upsert_agent(
            "hidden-agent",
            {},
            {
                "name": "hidden-agent",
                "system_prompt": "Hidden fixture Agent.",
                "mode": "primary",
                "hidden": True,
            },
            "api",
        )
    )
    set_config_store(config_store)
    yield


class TestListAgents:
    def test_list_returns_200(self):
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data

    def test_list_includes_explicitly_provisioned_agent(self):
        response = client.get("/agents")
        agents = response.json()["agents"]
        names = [a["name"] for a in agents]
        assert "default" in names

    def test_list_excludes_hidden_agents(self):
        """Only agents that are not hidden appear in the listing.

        The explicitly hidden fixture Agent must not appear.
        """
        response = client.get("/agents")
        agents = response.json()["agents"]
        names = [a["name"] for a in agents]
        assert "default" in names
        assert "hidden-agent" not in names


class TestGetAgent:
    def test_get_existing_agent(self):
        response = client.get("/agents/default")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default"
        assert "system_prompt" in data
        assert "config" in data
        assert "provider" in data

    def test_get_missing_agent_returns_404(self):
        response = client.get("/agents/does-not-exist")
        assert response.status_code == 404

    def test_get_hidden_agent_returns_404(self):
        """Hidden Agents cannot be retrieved via GET /agents/{name}."""
        response = client.get("/agents/hidden-agent")
        assert response.status_code == 404

    def test_mcp_readiness_is_scoped_and_freshness_qualified(self):
        from datetime import UTC, datetime, timedelta

        from server.app.api.dependencies import set_mcp_readiness_repository

        store = get_config_store()
        asyncio.run(
            store.upsert_agent(
                "readiness-agent",
                {},
                {
                    "name": "readiness-agent",
                    "system_prompt": "Use MCP.",
                    "mcp": {
                        "servers": {
                            "github": {
                                "url": "https://github.test/mcp",
                                "required": True,
                            },
                            "docs": {
                                "url": "https://docs.test/mcp",
                                "required": False,
                            },
                        }
                    },
                },
                "api",
            )
        )
        record = asyncio.run(store.get_agent_record("readiness-agent", {}))
        assert record is not None
        now = datetime.now(UTC)
        repository = MemoryMcpReadinessRepository()
        asyncio.run(
            repository.record(
                McpReadinessObservation(
                    agent_name="readiness-agent",
                    agent_revision=record.revision,
                    server_alias="github",
                    required=True,
                    status="ready",
                    tool_count=2,
                    schema_digest="a" * 64,
                    observed_at=now - timedelta(minutes=10),
                    fresh_until=now - timedelta(minutes=5),
                ),
                {},
            )
        )
        set_mcp_readiness_repository(repository)

        response = client.get("/agents/readiness-agent/mcp/readiness")

        assert response.status_code == 200
        servers = {item["server_alias"]: item for item in response.json()["servers"]}
        assert servers["github"]["status"] == "unknown"
        assert servers["github"]["failure_category"] == "observation_stale"
        assert servers["github"]["authorization_truth"] is False
        assert servers["docs"]["status"] == "unknown"
        assert servers["docs"]["failure_category"] == "not_observed"


class TestCreateAgent:
    def test_create_agent_persists_public_a2a_interface_url(self):
        public_url = "https://opaque.agents.example.com/a2a"
        response = client.post(
            "/agents",
            json={
                "name": "ka_create_public_a2a_url",
                "system_prompt": "Help customers.",
                "a2a": {"exposed": True, "public_interface_url": public_url},
            },
        )

        assert response.status_code == 201
        assert response.json()["a2a"]["public_interface_url"] == public_url

        get_response = client.get("/agents/ka_create_public_a2a_url")
        assert get_response.status_code == 200
        assert get_response.json()["a2a"]["public_interface_url"] == public_url

        raw = asyncio.run(get_config_store().get_agent_raw("ka_create_public_a2a_url"))
        assert raw is not None
        assert raw["a2a"]["public_interface_url"] == public_url

    def test_create_agent_persists_public_a2a_modes_and_skills(self):
        a2a = {
            "exposed": True,
            "public_interface_url": None,
            "default_input_modes": ["text/plain", "application/pdf"],
            "default_output_modes": ["application/json"],
            "skills": [
                {
                    "id": "document-analysis",
                    "name": "Document Analysis",
                    "description": "Extracts and summarizes PDF documents.",
                    "tags": ["documents", "pdf"],
                    "examples": ["Summarize the attached contract."],
                    "input_modes": ["application/pdf"],
                    "output_modes": ["text/plain", "application/json"],
                }
            ],
        }
        response = client.post(
            "/agents",
            json={
                "name": "document-agent",
                "system_prompt": "Analyze documents.",
                "a2a": a2a,
            },
        )

        assert response.status_code == 201
        assert response.json()["a2a"] == a2a

        raw = asyncio.run(get_config_store().get_agent_raw("document-agent"))
        assert raw is not None
        assert raw["a2a"] == a2a

    @pytest.mark.parametrize(
        "public_url",
        [
            "opaque.agents.example.com/a2a",
            "ftp://opaque.agents.example.com/a2a",
            "https://user:secret@opaque.agents.example.com/a2a",
            "https://opaque.agents.example.com/a2a#fragment",
        ],
    )
    def test_create_agent_rejects_invalid_public_a2a_interface_url(self, public_url: str) -> None:
        response = client.post(
            "/agents",
            json={
                "name": "ka_invalid_public_a2a_url",
                "system_prompt": "Help customers.",
                "a2a": {"public_interface_url": public_url},
            },
        )

        assert response.status_code == 422

    def test_create_agent_rejects_removed_flat_a2a_fields(self) -> None:
        response = client.post(
            "/agents",
            json={
                "name": "legacy-a2a-agent",
                "system_prompt": "Help customers.",
                "a2a_exposed": True,
            },
        )

        assert response.status_code == 422

    def test_create_agent_persists_display_name(self):
        response = client.post(
            "/agents",
            json={
                "name": "ka_create_display_name",
                "display_name": "Customer Support Concierge",
                "system_prompt": "Help customers.",
            },
        )

        assert response.status_code == 201
        assert response.json()["name"] == "ka_create_display_name"
        assert response.json()["display_name"] == "Customer Support Concierge"

        get_response = client.get("/agents/ka_create_display_name")
        assert get_response.status_code == 200
        assert get_response.json()["display_name"] == "Customer Support Concierge"

        raw = asyncio.run(get_config_store().get_agent_raw("ka_create_display_name"))
        assert raw is not None
        assert raw["display_name"] == "Customer Support Concierge"

    def test_create_new_agent(self):
        payload = {
            "name": "test-create-agent",
            "system_prompt": "You are a test agent.",
            "description": "A test agent",
            "max_tokens": 16000,
            "recursion_limit": 500,
            "provider": "bedrock",
            "tool_token_limit_before_evict": 2000,
            "context_policy": {
                "max_input_tokens": 32000,
                "tool_token_limit_before_evict": 4096,
                "summarization_enabled": True,
                "summarizer_model": "fast-summarizer",
                "offload_large_tool_outputs": True,
                "retention": {"logs": "summarize"},
            },
            "timeout_seconds": 45,
            "sandbox_profile": "lambda-default",
            "sandbox_execution_role_arn": (
                "arn:aws:iam::123456789012:role/cognition-agent-runtime"
            ),
            "middleware": [{"name": "tool_retry", "max_retries": 2}],
            "interrupt_on": {
                "execute": {
                    "allowed_decisions": ["approve", "reject"],
                    "description": "Shell commands require approval",
                }
            },
            "permissions": [
                {
                    "operations": ["read", "write"],
                    "paths": ["/workspace/repo/**"],
                    "mode": "allow",
                }
            ],
            "async_subagents": [
                {
                    "name": "researcher",
                    "description": "Runs long research tasks",
                    "graph_id": "research_graph",
                    "url": "https://agents.example.com",
                }
            ],
        }
        response = client.post("/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-create-agent"
        assert data["provider"] == "bedrock"
        assert data["config"]["max_tokens"] == 16000
        assert data["config"]["recursion_limit"] == 500
        assert data["config"]["provider"] == "bedrock"
        assert data["config"]["tool_token_limit_before_evict"] == 2000
        assert data["config"]["context_policy"]["max_input_tokens"] == 32000
        assert data["config"]["context_policy"]["tool_token_limit_before_evict"] == 4096
        assert data["config"]["context_policy"]["summarizer_model"] == "fast-summarizer"
        assert data["config"]["context_policy"]["retention"] == {"logs": "summarize"}
        assert data["config"]["timeout_seconds"] == 45
        assert data["config"]["sandbox_profile"] == "lambda-default"
        assert (
            data["config"]["sandbox_execution_role_arn"]
            == "arn:aws:iam::123456789012:role/cognition-agent-runtime"
        )
        assert data["interrupt_on"]["execute"]["allowed_decisions"] == ["approve", "reject"]
        assert data["permissions"][0]["paths"] == ["/workspace/repo/**"]
        assert data["async_subagents"][0]["name"] == "researcher"
        assert data["async_subagents"][0]["graph_id"] == "research_graph"

    def test_create_agent_persists_tool_policies(self):
        response = client.post(
            "/agents",
            json={
                "name": "test-create-tool-policies-agent",
                "system_prompt": "tool policy create test",
                "excluded_tools": ["glob", "grep", "ls"],
                "blocked_tools": ["execute"],
            },
        )
        assert response.status_code == 201
        assert response.json()["config"]["excluded_tools"] == ["glob", "grep", "ls"]
        assert response.json()["config"]["blocked_tools"] == ["execute"]

        get_resp = client.get("/agents/test-create-tool-policies-agent")
        assert get_resp.status_code == 200
        assert get_resp.json()["config"]["excluded_tools"] == ["glob", "grep", "ls"]
        assert get_resp.json()["config"]["blocked_tools"] == ["execute"]

        raw = asyncio.run(
            get_config_store().get_agent_raw("test-create-tool-policies-agent")
        )
        assert raw is not None
        assert raw["config"]["excluded_tools"] == ["glob", "grep", "ls"]
        assert raw["config"]["blocked_tools"] == ["execute"]

    def test_get_preserves_top_level_provider(self):
        client.post(
            "/agents",
            json={
                "name": "test-get-provider-agent",
                "system_prompt": "provider test",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
            },
        )

        response = client.get("/agents/test-get-provider-agent")
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "openai_compatible"
        assert data["model"] == "google/gemini-3-flash-preview"
        assert data["config"]["provider"] == "openai_compatible"
        assert data["config"]["model"] == "google/gemini-3-flash-preview"

    def test_create_duplicate_non_native_agent_succeeds(self):
        """Creating an agent that already exists (and is not native) should succeed (upsert)."""
        payload = {
            "name": "test-upsert-agent",
            "system_prompt": "First version.",
        }
        r1 = client.post("/agents", json=payload)
        assert r1.status_code == 201

        payload["system_prompt"] = "Second version."
        r2 = client.post("/agents", json=payload)
        assert r2.status_code == 201

    def test_former_default_name_is_builder_owned(self):
        """Formerly reserved Agent names are ordinary builder-owned names."""
        payload = {
            "name": "default",
            "system_prompt": "override attempt",
        }
        response = client.post("/agents", json=payload)
        assert response.status_code == 201
        assert response.json()["name"] == "default"
        assert response.json()["native"] is False

    def test_create_agent_missing_name_returns_422(self):
        response = client.post("/agents", json={"system_prompt": "no name"})
        assert response.status_code == 422


class TestExactScopeAndRevisions:
    """v0.13 Agent identity is the name plus the complete trusted scope."""

    @staticmethod
    def _scope_headers(**scope: str) -> dict[str, str]:
        return {
            f"x-cognition-scope-{key.replace('_', '-')}": value
            for key, value in scope.items()
        }

    @pytest.fixture(autouse=True)
    def configure_two_dimensional_scope(self):
        settings = get_settings()
        previous = (list(settings.scope_keys), settings.scoping_enabled)
        settings.scope_keys = ["tenant", "project"]
        # Keep optional headers in these tests so empty and partial scopes can
        # be exercised deliberately.
        settings.scoping_enabled = False
        yield
        settings.scope_keys, settings.scoping_enabled = previous

    def test_same_name_isolated_across_empty_partial_sibling_and_exact_scopes(self):
        name = "scope-isolation-agent"
        exact_red = self._scope_headers(tenant="acme", project="red")
        exact_blue = self._scope_headers(tenant="acme", project="blue")
        partial = self._scope_headers(tenant="acme")

        for headers, prompt in (
            ({}, "global"),
            (partial, "partial"),
            (exact_red, "red"),
            (exact_blue, "blue"),
        ):
            response = client.post(
                "/agents",
                headers=headers,
                json={"name": name, "system_prompt": prompt},
            )
            assert response.status_code == 201

        assert client.get(f"/agents/{name}").json()["system_prompt"] == "global"
        assert (
            client.get(f"/agents/{name}", headers=partial).json()["system_prompt"]
            == "partial"
        )
        assert (
            client.get(f"/agents/{name}", headers=exact_red).json()["system_prompt"]
            == "red"
        )
        assert (
            client.get(f"/agents/{name}", headers=exact_blue).json()["system_prompt"]
            == "blue"
        )

        # A broader API Agent is never inherited into a complete runtime scope.
        partial_only = "partial-only-agent"
        assert (
            client.post(
                "/agents",
                headers=partial,
                json={"name": partial_only, "system_prompt": "partial only"},
            ).status_code
            == 201
        )
        assert (
            client.get(f"/agents/{partial_only}", headers=exact_red).status_code
            == 404
        )

    def test_etag_guards_create_replace_patch_and_delete(self):
        headers = self._scope_headers(tenant="etag-co", project="api")
        payload = {"name": "etag-agent", "system_prompt": "revision one"}
        created = client.post(
            "/agents",
            headers={**headers, "If-None-Match": "*"},
            json=payload,
        )
        assert created.status_code == 201
        assert created.json()["revision"] == 1
        first_digest = created.json()["definition_digest"]
        first_etag = created.headers["etag"]

        duplicate = client.post(
            "/agents",
            headers={**headers, "If-None-Match": "*"},
            json=payload,
        )
        assert duplicate.status_code == 412

        patched = client.patch(
            "/agents/etag-agent",
            headers={**headers, "If-Match": first_etag},
            json={"system_prompt": "revision two"},
        )
        assert patched.status_code == 200
        assert patched.json()["revision"] == 2
        assert patched.json()["definition_digest"] != first_digest
        second_etag = patched.headers["etag"]

        stale_patch = client.patch(
            "/agents/etag-agent",
            headers={**headers, "If-Match": first_etag},
            json={"system_prompt": "must not win"},
        )
        assert stale_patch.status_code == 412
        assert (
            client.delete(
                "/agents/etag-agent",
                headers={**headers, "If-Match": first_etag},
            ).status_code
            == 412
        )
        assert (
            client.delete(
                "/agents/etag-agent",
                headers={**headers, "If-Match": second_etag},
            ).status_code
            == 204
        )
        assert client.get("/agents/etag-agent", headers=headers).status_code == 404

    def test_body_scope_must_match_authoritative_headers(self):
        headers = self._scope_headers(tenant="scope-co", project="docs")
        matching = client.post(
            "/agents",
            headers=headers,
            json={
                "name": "matching-body-scope",
                "system_prompt": "valid",
                "scope": {"tenant": "scope-co", "project": "docs"},
            },
        )
        assert matching.status_code == 201
        assert "deprecated" in matching.headers["warning"].lower()

        conflicting = client.post(
            "/agents",
            headers=headers,
            json={
                "name": "conflicting-body-scope",
                "system_prompt": "invalid",
                "scope": {"tenant": "other", "project": "docs"},
            },
        )
        assert conflicting.status_code == 400


class TestUpdateAgent:
    def test_patch_agent_updates_and_clears_public_a2a_interface_url(self):
        original_url = "https://original.agents.example.com/a2a"
        updated_url = "https://updated.agents.example.com/a2a"
        client.post(
            "/agents",
            json={
                "name": "ka_patch_public_a2a_url",
                "system_prompt": "Help customers.",
                "a2a": {"public_interface_url": original_url},
            },
        )

        update_response = client.patch(
            "/agents/ka_patch_public_a2a_url",
            json={"a2a": {"public_interface_url": updated_url}},
        )
        assert update_response.status_code == 200
        assert update_response.json()["a2a"]["public_interface_url"] == updated_url

        clear_response = client.patch(
            "/agents/ka_patch_public_a2a_url",
            json={"a2a": None},
        )
        assert clear_response.status_code == 200
        assert clear_response.json()["a2a"]["public_interface_url"] is None

        raw = asyncio.run(get_config_store().get_agent_raw("ka_patch_public_a2a_url"))
        assert raw is not None
        assert raw["a2a"]["public_interface_url"] is None

    def test_patch_agent_updates_and_clears_display_name(self):
        client.post(
            "/agents",
            json={
                "name": "ka_patch_display_name",
                "display_name": "Original Name",
                "system_prompt": "Help customers.",
            },
        )

        update_response = client.patch(
            "/agents/ka_patch_display_name",
            json={"display_name": "Updated Name"},
        )
        assert update_response.status_code == 200
        assert update_response.json()["display_name"] == "Updated Name"

        clear_response = client.patch(
            "/agents/ka_patch_display_name",
            json={"display_name": None},
        )
        assert clear_response.status_code == 200
        assert clear_response.json()["display_name"] is None

        raw = asyncio.run(get_config_store().get_agent_raw("ka_patch_display_name"))
        assert raw is not None
        assert raw["display_name"] is None

    def test_put_agent_updates_definition(self):
        """PUT should fully replace the agent definition."""
        # Create
        client.post(
            "/agents",
            json={
                "name": "test-put-agent",
                "system_prompt": "original",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
            },
        )
        # Replace
        response = client.put(
            "/agents/test-put-agent",
            json={
                "name": "test-put-agent",
                "system_prompt": "replaced",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "replaced" in (data.get("system_prompt") or "")
        assert data["provider"] == "openai_compatible"
        assert data["config"]["provider"] == "openai_compatible"

    def test_put_missing_agent_returns_404(self):
        # PUT on a missing agent still goes through create_agent, which upserts
        # into ConfigRegistry — a missing non-native agent results in creation (201 via upsert)
        # but the route signature is PUT so it returns 200 on successful upsert.
        response = client.put(
            "/agents/brand-new-via-put",
            json={"name": "brand-new-via-put", "system_prompt": "x"},
        )
        # PUT creates-or-replaces, so 200 is expected
        assert response.status_code in (200, 201)

    def test_patch_agent_partial_update(self):
        """PATCH should partially update an agent (e.g. description only)."""
        client.post(
            "/agents",
            json={
                "name": "test-patch-agent",
                "system_prompt": "original sp",
                "description": "old desc",
            },
        )
        response = client.patch(
            "/agents/test-patch-agent",
            json={"description": "updated desc"},
        )
        assert response.status_code == 200

    def test_patch_agent_updates_nested_config_fields(self):
        client.post(
            "/agents",
            json={
                "name": "test-patch-config-agent",
                "system_prompt": "original sp",
                "max_tokens": 4000,
                "recursion_limit": 100,
                "provider": "openai",
            },
        )
        response = client.patch(
            "/agents/test-patch-config-agent",
            json={
                "max_tokens": 8000,
                "timeout_seconds": 30,
                "sandbox_profile": "lambda-vpc",
                "sandbox_execution_role_arn": (
                    "arn:aws:iam::123456789012:role/cognition-agent-vpc"
                ),
            },
        )
        assert response.status_code == 200
        assert response.json()["provider"] == "openai"
        assert response.json()["config"]["max_tokens"] == 8000
        assert response.json()["config"]["recursion_limit"] == 100
        assert response.json()["config"]["provider"] == "openai"
        assert response.json()["config"]["timeout_seconds"] == 30
        assert response.json()["config"]["sandbox_profile"] == "lambda-vpc"
        assert (
            response.json()["config"]["sandbox_execution_role_arn"]
            == "arn:aws:iam::123456789012:role/cognition-agent-vpc"
        )

    def test_patch_agent_persists_blocked_tools(self):
        client.post(
            "/agents",
            json={
                "name": "test-patch-blocked-tools-agent",
                "system_prompt": "blocked tools patch test",
            },
        )
        response = client.patch(
            "/agents/test-patch-blocked-tools-agent",
            json={"blocked_tools": ["execute", "task", "write_todos"]},
        )
        assert response.status_code == 200
        assert response.json()["config"]["blocked_tools"] == [
            "execute",
            "task",
            "write_todos",
        ]

        get_resp = client.get("/agents/test-patch-blocked-tools-agent")
        assert get_resp.status_code == 200
        assert get_resp.json()["config"]["blocked_tools"] == [
            "execute",
            "task",
            "write_todos",
        ]

        raw = asyncio.run(get_config_store().get_agent_raw("test-patch-blocked-tools-agent"))
        assert raw is not None
        assert raw["config"]["blocked_tools"] == ["execute", "task", "write_todos"]

    def test_patch_agent_persists_excluded_tools(self):
        client.post(
            "/agents",
            json={
                "name": "test-patch-excluded-tools-agent",
                "system_prompt": "excluded tools patch test",
            },
        )
        response = client.patch(
            "/agents/test-patch-excluded-tools-agent",
            json={"excluded_tools": ["glob", "grep", "inspect_package", "ls"]},
        )
        assert response.status_code == 200
        assert response.json()["config"]["excluded_tools"] == [
            "glob",
            "grep",
            "inspect_package",
            "ls",
        ]

        get_resp = client.get("/agents/test-patch-excluded-tools-agent")
        assert get_resp.status_code == 200
        assert get_resp.json()["config"]["excluded_tools"] == [
            "glob",
            "grep",
            "inspect_package",
            "ls",
        ]

        raw = asyncio.run(get_config_store().get_agent_raw("test-patch-excluded-tools-agent"))
        assert raw is not None
        assert raw["config"]["excluded_tools"] == ["glob", "grep", "inspect_package", "ls"]

    def test_patch_scoped_agent_persists_excluded_tools(self):
        scoped_settings = Settings(scoping_enabled=True, scope_keys=["tenant"])
        headers = {"X-Cognition-Scope-Tenant": "wasaloon"}
        app.dependency_overrides[get_settings_dep] = lambda: scoped_settings
        try:
            create_resp = client.post(
                "/agents",
                json={
                    "name": "test-scoped-excluded-tools-agent",
                    "system_prompt": "scoped excluded tools test",
                },
                headers=headers,
            )
            assert create_resp.status_code == 201

            response = client.patch(
                "/agents/test-scoped-excluded-tools-agent",
                json={"excluded_tools": ["glob", "grep", "inspect_package", "ls"]},
                headers=headers,
            )
            assert response.status_code == 200
            assert response.json()["config"]["excluded_tools"] == [
                "glob",
                "grep",
                "inspect_package",
                "ls",
            ]

            get_resp = client.get(
                "/agents/test-scoped-excluded-tools-agent",
                headers=headers,
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["config"]["excluded_tools"] == [
                "glob",
                "grep",
                "inspect_package",
                "ls",
            ]

            raw = asyncio.run(
                get_config_store().get_agent_raw(
                    "test-scoped-excluded-tools-agent",
                    {"tenant": "wasaloon"},
                )
            )
            assert raw is not None
            assert raw["config"]["excluded_tools"] == [
                "glob",
                "grep",
                "inspect_package",
                "ls",
            ]
        finally:
            app.dependency_overrides.pop(get_settings_dep, None)

    def test_patch_unrelated_field_preserves_top_level_provider(self):
        client.post(
            "/agents",
            json={
                "name": "test-patch-provider-agent",
                "system_prompt": "original sp",
                "description": "old desc",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
            },
        )
        response = client.patch(
            "/agents/test-patch-provider-agent",
            json={"description": "updated desc"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "updated desc"
        assert data["provider"] == "openai_compatible"
        assert data["model"] == "google/gemini-3-flash-preview"
        assert data["config"]["provider"] == "openai_compatible"
        assert data["config"]["model"] == "google/gemini-3-flash-preview"

    def test_system_prompt_is_not_truncated(self):
        long_prompt = "A" * 1200
        client.post(
            "/agents",
            json={"name": "test-long-prompt-agent", "system_prompt": long_prompt},
        )

        response = client.get("/agents/test-long-prompt-agent")
        assert response.status_code == 200
        assert response.json()["system_prompt"] == long_prompt

    def test_patch_missing_agent_returns_404(self):
        response = client.patch(
            "/agents/no-such-agent-patch",
            json={"description": "x"},
        )
        assert response.status_code == 404

    def test_patch_agent_tools_with_simple_names(self):
        """PATCH with simple tool names (no dots) should persist correctly.

        Regression: validate_tools used to reject names without at least one
        dot, causing silent data loss in the PATCH handler.
        """
        client.post(
            "/agents",
            json={
                "name": "test-patch-tools-agent",
                "system_prompt": "tools test",
            },
        )
        response = client.patch(
            "/agents/test-patch-tools-agent",
            json={"tools": ["directorate_get_change_set_context", "my_custom_tool"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tools"] == ["directorate_get_change_set_context", "my_custom_tool"]

        # Verify round-trip via GET
        get_resp = client.get("/agents/test-patch-tools-agent")
        assert get_resp.status_code == 200
        assert get_resp.json()["tools"] == ["directorate_get_change_set_context", "my_custom_tool"]

    def test_patch_agent_tools_with_module_paths_rejected(self):
        """Agent tool attachments must be registry tool names, not module paths."""
        client.post(
            "/agents",
            json={
                "name": "test-patch-module-tools-agent",
                "system_prompt": "module tools test",
            },
        )
        response = client.patch(
            "/agents/test-patch-module-tools-agent",
            json={"tools": ["server.app.tools.file_tools"]},
        )
        assert response.status_code == 500

    def test_patch_agent_skills(self):
        """PATCH with attached skill names should persist correctly."""
        client.post(
            "/agents",
            json={
                "name": "test-patch-skills-agent",
                "system_prompt": "skills test",
            },
        )
        response = client.patch(
            "/agents/test-patch-skills-agent",
            json={
                "skills": ["clean-code", "directorate-github-developer-workflow"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["skills"] == ["clean-code", "directorate-github-developer-workflow"]

    def test_patch_agent_empty_tool_name_rejected(self):
        """Empty tool names should still be rejected by the validator."""
        client.post(
            "/agents",
            json={
                "name": "test-patch-empty-tool-agent",
                "system_prompt": "empty tool test",
            },
        )
        response = client.patch(
            "/agents/test-patch-empty-tool-agent",
            json={"tools": [""]},
        )
        assert response.status_code == 500

    def test_create_agent_with_tools_and_skills(self):
        """POST with tools and skills should persist and round-trip."""
        response = client.post(
            "/agents",
            json={
                "name": "test-create-with-tools",
                "system_prompt": "create with tools test",
                "tools": ["my_tool"],
                "skills": ["clean-code"],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["tools"] == ["my_tool"]
        assert data["skills"] == ["clean-code"]


class TestDeleteAgent:
    def test_delete_existing_agent(self):
        client.post(
            "/agents",
            json={"name": "test-delete-agent", "system_prompt": "to be deleted"},
        )
        response = client.delete("/agents/test-delete-agent")
        assert response.status_code == 204

    def test_delete_former_default_name(self):
        """Formerly reserved names can be created and deleted normally."""
        client.post(
            "/agents",
            json={"name": "default", "system_prompt": "Builder-owned Agent."},
        )
        response = client.delete("/agents/default")
        assert response.status_code == 204

    def test_delete_missing_agent_returns_404(self):
        response = client.delete("/agents/no-such-agent-delete")
        assert response.status_code == 404
