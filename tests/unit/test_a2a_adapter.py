"""Unit tests for the A2A protocol adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import Role, TaskState

from server.app.agent.definition import AgentDefinition
from server.app.agent.runtime import (
    DoneEvent,
    ErrorEvent,
    RunStateEvent,
    TokenEvent,
)
from server.app.protocols.a2a.card import build_agent_card
from server.app.protocols.a2a.mapping import (
    _RUN_STATUS_TO_A2A,
    event_to_a2a_state,
    extract_text_from_parts,
    is_hitl_pause,
)


class TestBuildAgentCard:
    def test_primary_agents_become_skills(self):
        agents = [
            AgentDefinition(
                name="default",
                system_prompt="test",
                mode="primary",
                description="Main coding agent",
            ),
            AgentDefinition(
                name="researcher",
                system_prompt="test",
                mode="subagent",
            ),
        ]
        card = build_agent_card(agents, "http://localhost:8000", "0.10.0")
        assert card.name == "Cognition"
        assert len(card.skills) == 1
        assert card.skills[0].id == "default"
        assert card.skills[0].description == "Main coding agent"

    def test_hidden_agents_excluded(self):
        agents = [
            AgentDefinition(name="internal", system_prompt="test", mode="primary", hidden=True),
            AgentDefinition(name="visible", system_prompt="test", mode="primary"),
        ]
        card = build_agent_card(agents, "http://localhost:8000", "0.10.0")
        assert len(card.skills) == 1
        assert card.skills[0].id == "visible"

    def test_empty_agents_get_default_skill(self):
        card = build_agent_card([], "http://localhost:8000", "0.10.0")
        assert len(card.skills) == 1
        assert card.skills[0].id == "default"

    def test_card_has_streaming_capability(self):
        card = build_agent_card([], "http://localhost:8000", "0.10.0")
        assert card.capabilities.streaming is True

    def test_card_has_jsonrpc_interface(self):
        card = build_agent_card([], "http://localhost:8000", "0.10.0")
        assert len(card.supported_interfaces) == 1
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
        assert card.supported_interfaces[0].url == "http://localhost:8000/a2a"

    def test_multiple_primary_agents(self):
        agents = [
            AgentDefinition(name="coder", system_prompt="test", mode="primary"),
            AgentDefinition(name="reviewer", system_prompt="test", mode="primary"),
        ]
        card = build_agent_card(agents, "http://localhost:8000", "0.10.0")
        assert len(card.skills) == 2
        names = [s.id for s in card.skills]
        assert "coder" in names
        assert "reviewer" in names


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


class TestEventToA2AState:
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
            "queued", "starting", "active", "idle",
            "waiting_for_approval", "stalled",
            "done", "failed", "aborted", "aborting", "expired",
        }
        assert set(_RUN_STATUS_TO_A2A.keys()) == expected

    def test_terminal_states(self):
        assert _RUN_STATUS_TO_A2A["done"] == TaskState.TASK_STATE_COMPLETED
        assert _RUN_STATUS_TO_A2A["failed"] == TaskState.TASK_STATE_FAILED
        assert _RUN_STATUS_TO_A2A["aborted"] == TaskState.TASK_STATE_CANCELED
