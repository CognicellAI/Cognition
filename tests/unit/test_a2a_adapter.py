"""Unit tests for the A2A protocol adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from a2a.helpers.proto_helpers import new_text_artifact
from a2a.types import TaskState
from fastapi import FastAPI

from server.app.agent.definition import A2AConfig, A2APublicSkill, AgentDefinition
from server.app.agent.runtime import (
    DoneEvent,
    ErrorEvent,
    RunStateEvent,
    TokenEvent,
)
from server.app.protocols.a2a.card import build_agent_card_for_agent
from server.app.protocols.a2a.mapping import (
    _RUN_STATUS_TO_A2A,
    event_to_a2a_state,
    is_hitl_pause,
)
from server.app.protocols.a2a.routes import _task_signature


class TestA2AConfig:
    def test_default_a2a_exposure_is_false(self):
        agent = AgentDefinition(name="test", system_prompt="test prompt")
        assert agent.a2a.exposed is False

    def test_a2a_exposure_can_be_set_true(self):
        agent = AgentDefinition(
            name="test", system_prompt="test prompt", a2a=A2AConfig(exposed=True)
        )
        assert agent.a2a.exposed is True

    def test_a2a_exposure_can_be_set_false_explicitly(self):
        agent = AgentDefinition(
            name="test", system_prompt="test prompt", a2a=A2AConfig(exposed=False)
        )
        assert agent.a2a.exposed is False

    def test_rejects_invalid_media_type(self):
        with pytest.raises(ValueError, match="Invalid A2A media type"):
            A2AConfig(default_input_modes=["pdf"])

    def test_rejects_duplicate_public_skill_ids(self):
        skill = A2APublicSkill(
            id="primary",
            name="Primary",
            description="Primary capability",
            tags=["primary"],
        )
        with pytest.raises(ValueError, match="skill IDs must be unique"):
            A2AConfig(skills=[skill, skill])


class TestBuildAgentCardForAgent:
    def test_card_uses_agent_name_as_title(self):
        agent = AgentDefinition(
            name="my-agent", system_prompt="test", mode="primary", a2a=A2AConfig(exposed=True)
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert card.name == "my-agent"

    def test_card_uses_display_name_for_public_presentation(self):
        agent = AgentDefinition(
            name="ka_0cadedacd0b74a509358b48b5e3fd952",
            display_name="Customer Support Concierge",
            system_prompt="test",
            mode="primary",
            a2a=A2AConfig(exposed=True),
        )

        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")

        assert card.name == "Customer Support Concierge"
        assert card.description == "Agent: Customer Support Concierge"
        assert len(card.skills) == 1
        assert card.skills[0].id == "primary"
        assert card.skills[0].name == "Customer Support Concierge"
        assert card.skills[0].description == ("Primary capability for Customer Support Concierge")
        assert card.skills[0].tags == ["primary"]

    def test_card_has_correct_endpoint(self):
        agent = AgentDefinition(
            name="my-agent", system_prompt="test", mode="primary", a2a=A2AConfig(exposed=True)
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert len(card.supported_interfaces) == 1
        assert card.supported_interfaces[0].url == "http://localhost:8000/a2a/my-agent"
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"

    def test_card_uses_configured_public_interface_url_exactly(self):
        public_url = "https://opaque.agents.example.com/a2a?channel=public"
        agent = AgentDefinition(
            name="ka_private_runtime_name",
            display_name="Customer Support Concierge",
            system_prompt="test",
            mode="primary",
            a2a=A2AConfig(exposed=True, public_interface_url=public_url),
        )

        card = build_agent_card_for_agent(agent, "http://private:8000", "0.12.0-rc.3")

        assert card.supported_interfaces[0].url == public_url
        assert "ka_private_runtime_name" not in card.supported_interfaces[0].url

    def test_card_has_streaming_capability(self):
        agent = AgentDefinition(
            name="test", system_prompt="test", mode="primary", a2a=A2AConfig(exposed=True)
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert card.capabilities.streaming is True

    def test_card_advertises_generic_text_and_json_inputs(self):
        agent = AgentDefinition(
            name="test", system_prompt="test", mode="primary", a2a=A2AConfig(exposed=True)
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")

        assert card.default_input_modes == ["text/plain", "application/json"]
        assert card.skills[0].input_modes == ["text/plain", "application/json"]

    def test_card_publishes_builder_configured_modes_and_skills(self):
        agent = AgentDefinition(
            name="document-agent",
            display_name="Document Intelligence",
            system_prompt="test",
            mode="primary",
            a2a=A2AConfig(
                exposed=True,
                default_input_modes=["text/plain", "application/pdf"],
                default_output_modes=["application/json"],
                skills=[
                    A2APublicSkill(
                        id="document-analysis",
                        name="Document Analysis",
                        description="Extracts and summarizes PDF documents.",
                        tags=["documents", "pdf"],
                        examples=["Summarize the attached contract."],
                        input_modes=["application/pdf"],
                        output_modes=["text/plain", "application/json"],
                    )
                ],
            ),
        )

        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.12.0")

        assert card.default_input_modes == ["text/plain", "application/pdf"]
        assert card.default_output_modes == ["application/json"]
        assert len(card.skills) == 1
        assert card.skills[0].id == "document-analysis"
        assert card.skills[0].examples == ["Summarize the attached contract."]
        assert card.skills[0].input_modes == ["application/pdf"]
        assert card.skills[0].output_modes == ["text/plain", "application/json"]

    def test_card_has_single_skill(self):
        agent = AgentDefinition(
            name="coder",
            system_prompt="test",
            mode="primary",
            description="Coding assistant",
            a2a=A2AConfig(exposed=True),
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert len(card.skills) == 1
        assert card.skills[0].id == "coder"
        assert card.skills[0].description == "Coding assistant"

    def test_card_uses_default_description_when_none(self):
        agent = AgentDefinition(
            name="coder", system_prompt="test", mode="primary", a2a=A2AConfig(exposed=True)
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert card.skills[0].description == "Cognition agent: coder"

    def test_card_does_not_expose_system_prompt(self):
        agent = AgentDefinition(
            name="coder",
            system_prompt="SECRET PROMPT TEXT",
            mode="primary",
            a2a=A2AConfig(exposed=True),
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        card_str = str(card)
        assert "SECRET PROMPT TEXT" not in card_str

    def test_card_has_no_legacy_tools_surface(self):
        agent = AgentDefinition(
            name="coder",
            system_prompt="test",
            mode="primary",
            a2a=A2AConfig(exposed=True),
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        card_str = str(card)
        assert "tools" not in card_str.lower()


class TestA2APerAgentCardRoute:
    @pytest.mark.asyncio
    async def test_per_agent_card_uses_request_base_url_and_scope(self):
        from server.app.protocols.a2a.routes import mount_a2a_routes
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        app = FastAPI()
        settings = MagicMock()
        settings.scope_keys = ["user"]
        config_store = DefaultConfigStore(MemoryConfigRegistry())
        await config_store.upsert_agent(
            "scoped-agent",
            {"user": "alice"},
            {
                "name": "scoped-agent",
                "system_prompt": "test",
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
            version="0.10.3",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://example.test",
        ) as client:
            response = await client.get(
                "/a2a/scoped-agent/.well-known/agent-card.json",
                headers={"X-Cognition-Scope-User": "alice"},
            )

        assert response.status_code == 200
        card = response.json()
        assert card["name"] == "scoped-agent"
        assert card["supportedInterfaces"][0]["url"] == ("http://example.test/a2a/scoped-agent")

    @pytest.mark.asyncio
    async def test_per_agent_card_respects_request_scope(self):
        from server.app.protocols.a2a.routes import mount_a2a_routes
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        app = FastAPI()
        settings = MagicMock()
        settings.scope_keys = ["user"]
        config_store = DefaultConfigStore(MemoryConfigRegistry())
        await config_store.upsert_agent(
            "scoped-agent",
            {"user": "alice"},
            {
                "name": "scoped-agent",
                "system_prompt": "test",
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
            version="0.10.3",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://example.test",
        ) as client:
            response = await client.get(
                "/a2a/scoped-agent/.well-known/agent-card.json",
                headers={"X-Cognition-Scope-User": "bob"},
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_per_agent_card_uses_configured_public_interface_url(self):
        from server.app.protocols.a2a.routes import mount_a2a_routes
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        app = FastAPI()
        settings = MagicMock()
        settings.scope_keys = []
        config_store = DefaultConfigStore(MemoryConfigRegistry())
        public_url = "https://opaque.agents.example.com/a2a"
        await config_store.upsert_agent(
            "ka_private_runtime_name",
            {},
            {
                "name": "ka_private_runtime_name",
                "display_name": "Customer Support Concierge",
                "system_prompt": "test",
                "mode": "primary",
                "a2a": {"exposed": True, "public_interface_url": public_url},
            },
        )
        await mount_a2a_routes(
            app,
            settings=settings,
            config_store=config_store,
            session_agent_manager=MagicMock(),
            store=MagicMock(),
            version="0.12.0-rc.3",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://private:8000",
        ) as client:
            response = await client.get("/a2a/ka_private_runtime_name/.well-known/agent-card.json")

        assert response.status_code == 200
        card = response.json()
        assert card["name"] == "Customer Support Concierge"
        assert card["supportedInterfaces"][0]["url"] == public_url


class TestA2AExposureFiltering:
    """Test the filtering logic that determines which agents are A2A-exposed."""

    def test_subagent_not_exposed(self):
        agent = AgentDefinition(
            name="sub", system_prompt="test", mode="subagent", a2a=A2AConfig(exposed=True)
        )
        # The filtering logic in routes.py checks mode != "subagent"
        assert agent.mode == "subagent"  # would be filtered out

    def test_hidden_agent_not_exposed(self):
        agent = AgentDefinition(
            name="hidden",
            system_prompt="test",
            mode="primary",
            hidden=True,
            a2a=A2AConfig(exposed=True),
        )
        assert agent.hidden is True  # would be filtered out

    def test_primary_non_hidden_exposed(self):
        agent = AgentDefinition(
            name="visible",
            system_prompt="test",
            mode="primary",
            hidden=False,
            a2a=A2AConfig(exposed=True),
        )
        assert agent.mode != "subagent" and not agent.hidden and agent.a2a.exposed

    def test_default_not_exposed(self):
        agent = AgentDefinition(name="default", system_prompt="test")
        assert agent.a2a.exposed is False


class TestExtractScope:
    def test_extracts_scope_headers(self):
        from server.app.protocols.a2a.routes import _extract_scope

        headers = {
            "x-cognition-scope-user": "tenant-a",
            "x-cognition-scope-project": "ios",
            "content-type": "application/json",
        }
        scope = _extract_scope(headers, ["user", "project"])
        assert scope == {"user": "tenant-a", "project": "ios"}

    def test_returns_none_when_no_scope_keys(self):
        from server.app.protocols.a2a.routes import _extract_scope

        headers = {"x-cognition-scope-user": "tenant-a"}
        scope = _extract_scope(headers, [])
        assert scope is None

    def test_returns_none_when_no_headers_match(self):
        from server.app.protocols.a2a.routes import _extract_scope

        headers = {"content-type": "application/json"}
        scope = _extract_scope(headers, ["user"])
        assert scope is None


class TestExecutorAgentName:
    def test_custom_agent_name(self):
        from server.app.protocols.a2a.executor import CognitionA2AExecutor

        executor = CognitionA2AExecutor(
            runtime=MagicMock(),
            task_store=MagicMock(),
            session_agent_manager=MagicMock(),
            agent_name="my-agent",
        )
        assert executor._agent_name == "my-agent"


class TestEventToA2AState:
    def test_run_state_idle(self):
        event = RunStateEvent(from_status="active", to_status="idle")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_COMPLETED

    def test_run_state_done(self):
        event = RunStateEvent(from_status="active", to_status="done")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_COMPLETED

    def test_run_state_failed(self):
        event = RunStateEvent(from_status="active", to_status="failed")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_FAILED

    def test_run_state_waiting_for_approval(self):
        event = RunStateEvent(from_status="active", to_status="waiting_for_approval")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_INPUT_REQUIRED

    def test_run_state_aborted(self):
        event = RunStateEvent(from_status="active", to_status="aborted")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_CANCELED

    def test_run_state_active(self):
        event = RunStateEvent(from_status="starting", to_status="active")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_WORKING

    def test_done_event(self):
        event = DoneEvent()
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_COMPLETED

    def test_error_event(self):
        event = ErrorEvent(message="fail")
        assert event_to_a2a_state(event) == TaskState.TASK_STATE_FAILED

    def test_token_event_returns_none(self):
        event = TokenEvent(content="hello")
        assert event_to_a2a_state(event) is None


class TestIsHitlPause:
    def test_waiting_for_approval_is_hitl(self):
        event = RunStateEvent(from_status="active", to_status="waiting_for_approval")
        assert is_hitl_pause(event) is True

    def test_done_is_not_hitl(self):
        event = RunStateEvent(from_status="active", to_status="done")
        assert is_hitl_pause(event) is False

    def test_token_is_not_hitl(self):
        event = TokenEvent(content="hello")
        assert is_hitl_pause(event) is False


class TestRunStatusMapping:
    def test_all_expected_statuses_mapped(self):
        expected = {
            "queued",
            "starting",
            "active",
            "idle",
            "waiting_for_approval",
            "interrupted",
            "stalled",
            "done",
            "failed",
            "rejected",
            "aborted",
            "aborting",
            "expired",
        }
        assert set(_RUN_STATUS_TO_A2A.keys()) == expected

    def test_terminal_states(self):
        assert _RUN_STATUS_TO_A2A["idle"] == TaskState.TASK_STATE_COMPLETED
        assert _RUN_STATUS_TO_A2A["done"] == TaskState.TASK_STATE_COMPLETED
        assert _RUN_STATUS_TO_A2A["failed"] == TaskState.TASK_STATE_FAILED
        assert _RUN_STATUS_TO_A2A["aborted"] == TaskState.TASK_STATE_CANCELED


def test_task_subscription_signature_detects_artifact_content_changes():
    from a2a.types import Task, TaskStatus

    task = Task(
        id="task-1",
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    before = _task_signature(task)
    task.artifacts.append(
        new_text_artifact(
            name="response",
            text="chunk one",
            artifact_id="artifact-1",
        )
    )
    after_first_chunk = _task_signature(task)
    task.artifacts[0].parts[0].text = "chunk one and two"

    assert after_first_chunk != before
    assert _task_signature(task) != after_first_chunk
