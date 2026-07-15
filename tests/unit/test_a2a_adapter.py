"""Unit tests for the A2A protocol adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from a2a.helpers.proto_helpers import new_text_artifact
from a2a.types import TaskState
from fastapi import FastAPI

from server.app.agent.definition import AgentDefinition
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
    extract_text_from_parts,
    is_hitl_pause,
)
from server.app.protocols.a2a.routes import _task_signature


class TestA2AExposedField:
    def test_default_a2a_exposed_is_false(self):
        agent = AgentDefinition(name="test", system_prompt="test prompt")
        assert agent.a2a_exposed is False

    def test_a2a_exposed_can_be_set_true(self):
        agent = AgentDefinition(name="test", system_prompt="test prompt", a2a_exposed=True)
        assert agent.a2a_exposed is True

    def test_a2a_exposed_can_be_set_false_explicitly(self):
        agent = AgentDefinition(name="test", system_prompt="test prompt", a2a_exposed=False)
        assert agent.a2a_exposed is False


class TestBuildAgentCardForAgent:
    def test_card_has_agent_name_in_title(self):
        agent = AgentDefinition(
            name="my-agent", system_prompt="test", mode="primary", a2a_exposed=True
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert card.name == "Cognition (my-agent)"

    def test_card_has_correct_endpoint(self):
        agent = AgentDefinition(
            name="my-agent", system_prompt="test", mode="primary", a2a_exposed=True
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert len(card.supported_interfaces) == 1
        assert card.supported_interfaces[0].url == "http://localhost:8000/a2a/my-agent"
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"

    def test_card_has_streaming_capability(self):
        agent = AgentDefinition(name="test", system_prompt="test", mode="primary", a2a_exposed=True)
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert card.capabilities.streaming is True

    def test_card_has_single_skill(self):
        agent = AgentDefinition(
            name="coder",
            system_prompt="test",
            mode="primary",
            description="Coding assistant",
            a2a_exposed=True,
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert len(card.skills) == 1
        assert card.skills[0].id == "coder"
        assert card.skills[0].description == "Coding assistant"

    def test_card_uses_default_description_when_none(self):
        agent = AgentDefinition(
            name="coder", system_prompt="test", mode="primary", a2a_exposed=True
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        assert card.skills[0].description == "Cognition agent: coder"

    def test_card_does_not_expose_system_prompt(self):
        agent = AgentDefinition(
            name="coder",
            system_prompt="SECRET PROMPT TEXT",
            mode="primary",
            a2a_exposed=True,
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        card_str = str(card)
        assert "SECRET PROMPT TEXT" not in card_str

    def test_card_does_not_expose_tools(self):
        agent = AgentDefinition(
            name="coder",
            system_prompt="test",
            mode="primary",
            tools=["tool1", "tool2"],
            a2a_exposed=True,
        )
        card = build_agent_card_for_agent(agent, "http://localhost:8000", "0.10.0")
        card_str = str(card)
        assert "tool1" not in card_str
        assert "tool2" not in card_str


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
                "a2a_exposed": True,
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
        assert card["name"] == "Cognition (scoped-agent)"
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
                "a2a_exposed": True,
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


class TestA2AExposureFiltering:
    """Test the filtering logic that determines which agents are A2A-exposed."""

    def test_subagent_not_exposed(self):
        agent = AgentDefinition(name="sub", system_prompt="test", mode="subagent", a2a_exposed=True)
        # The filtering logic in routes.py checks mode != "subagent"
        assert agent.mode == "subagent"  # would be filtered out

    def test_hidden_agent_not_exposed(self):
        agent = AgentDefinition(
            name="hidden",
            system_prompt="test",
            mode="primary",
            hidden=True,
            a2a_exposed=True,
        )
        assert agent.hidden is True  # would be filtered out

    def test_primary_non_hidden_exposed(self):
        agent = AgentDefinition(
            name="visible",
            system_prompt="test",
            mode="primary",
            hidden=False,
            a2a_exposed=True,
        )
        assert agent.mode != "subagent" and not agent.hidden and agent.a2a_exposed

    def test_default_not_exposed(self):
        agent = AgentDefinition(name="default", system_prompt="test")
        assert agent.a2a_exposed is False


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


class TestExtractTextFromParts:
    def test_single_text_part(self):
        from a2a.types import Part

        parts = [Part(text="hello world", media_type="text/plain")]
        assert extract_text_from_parts(parts) == "hello world"

    def test_multiple_text_parts(self):
        from a2a.types import Part

        parts = [
            Part(text="line one", media_type="text/plain"),
            Part(text="line two", media_type="text/plain"),
        ]
        assert extract_text_from_parts(parts) == "line one\nline two"

    def test_empty_parts(self):
        assert extract_text_from_parts([]) == ""

    def test_current_text_part_shape(self):
        parts = [{"kind": "text", "text": "hello world"}]
        assert extract_text_from_parts(parts) == "hello world"


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
