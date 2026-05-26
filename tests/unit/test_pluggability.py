from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from server.app.agent.cognition_agent import (
    CognitionAgentParams,
    clear_agent_cache,
    create_cognition_agent,
)
from server.app.agent.definition import AsyncSubagentConfig, ContextPolicy
from server.app.storage.config_models import GlobalAgentDefaults


class _DefaultsOnlyConfigStore:
    config_registry = None

    def __init__(self, defaults: GlobalAgentDefaults) -> None:
        self._defaults = defaults

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        return self._defaults


@pytest.mark.asyncio
async def test_create_cognition_agent_pluggability():
    """Verify that create_cognition_agent correctly applies pluggability parameters."""
    clear_agent_cache()
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()
        params = CognitionAgentParams(
            project_path=".",
            memory=["TEST_MEMORY.md"],
            skills=["clean-code"],
            subagents=[{"name": "test-subagent", "system_prompt": "..."}],
            interrupt_on={"execute": {"allowed_decisions": ["approve", "reject"]}},
            permissions=[
                {
                    "operations": ["read", "write"],
                    "paths": ["/workspace/repo/**"],
                    "mode": "allow",
                }
            ],
        )
        await create_cognition_agent(params)

        args, kwargs = mock_create.call_args
        assert kwargs["memory"] == ["TEST_MEMORY.md"]
        assert kwargs["skills"] == ["/skills/api/"]
        assert len(kwargs["subagents"]) == 1
        sa = kwargs["subagents"][0]
        assert sa["name"] == "test-subagent"
        assert sa["system_prompt"] == "..."
        assert sa["description"] == ""
        assert kwargs["interrupt_on"] == {"execute": {"allowed_decisions": ["approve", "reject"]}}
        assert kwargs["permissions"][0].operations == ["read", "write"]
        assert kwargs["permissions"][0].paths == ["/workspace/repo/**"]
        assert kwargs["permissions"][0].mode == "allow"

        middleware_names = [m.name for m in kwargs["middleware"]]
        assert "cognition_observability" in middleware_names
        assert "cognition_streaming" in middleware_names
    clear_agent_cache()


@pytest.mark.asyncio
async def test_context_policy_adds_deep_agents_summarization_tool_middleware():
    """ContextPolicy should align to Deep Agents' summarization tool primitive."""
    clear_agent_cache()
    summarization_tool = object()
    with (
        patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create,
        patch(
            "deepagents.middleware.summarization.create_summarization_tool_middleware",
            return_value=summarization_tool,
        ) as mock_summarization,
    ):
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                context_policy=ContextPolicy(summarization_enabled=True),
            )
        )

        _, kwargs = mock_create.call_args
        assert summarization_tool in kwargs["middleware"]
        mock_summarization.assert_called_once()
    clear_agent_cache()


@pytest.mark.asyncio
async def test_context_policy_can_disable_summarization_tool_middleware():
    """summarization_enabled=False should not attach summarization middleware."""
    clear_agent_cache()
    with (
        patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create,
        patch(
            "deepagents.middleware.summarization.create_summarization_tool_middleware"
        ) as mock_summarization,
    ):
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                context_policy=ContextPolicy(summarization_enabled=False),
            )
        )

        mock_summarization.assert_not_called()
    clear_agent_cache()


@pytest.mark.asyncio
async def test_context_policy_does_not_pass_removed_tool_token_kwarg():
    """Deep Agents 0.6.2 removed tool_token_limit_before_evict from create_deep_agent."""
    clear_agent_cache()
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                context_policy=ContextPolicy(
                    summarization_enabled=False,
                    tool_token_limit_before_evict=4096,
                ),
            )
        )

        _, kwargs = mock_create.call_args
        assert "tool_token_limit_before_evict" not in kwargs
    clear_agent_cache()


@pytest.mark.asyncio
async def test_context_policy_changes_agent_cache_key():
    """Context policy changes should recompile the Deep Agents graph."""
    clear_agent_cache()
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                context_policy=ContextPolicy(
                    summarization_enabled=False,
                    max_input_tokens=32000,
                ),
            )
        )
        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                context_policy=ContextPolicy(
                    summarization_enabled=False,
                    max_input_tokens=64000,
                ),
            )
        )

        assert mock_create.call_count == 2
    clear_agent_cache()


@pytest.mark.asyncio
async def test_rich_interrupt_on_changes_agent_cache_key():
    """Rich HITL config changes should recompile the Deep Agents graph."""
    clear_agent_cache()
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                interrupt_on={
                    "execute": {
                        "allowed_decisions": ["approve", "reject"],
                        "description": "Review commands",
                    }
                },
            )
        )
        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                interrupt_on={
                    "execute": {
                        "allowed_decisions": ["approve", "edit", "reject"],
                        "description": "Review and edit commands",
                    }
                },
            )
        )

        assert mock_create.call_count == 2
    clear_agent_cache()


@pytest.mark.asyncio
async def test_empty_interrupt_on_overrides_global_defaults():
    """Explicit interrupt_on={} should not inherit default approvals."""
    clear_agent_cache()
    defaults = GlobalAgentDefaults(
        interrupt_on={"execute": {"allowed_decisions": ["approve", "reject"]}}
    )
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                system_prompt="test",
                interrupt_on={},
                config_store=cast(Any, _DefaultsOnlyConfigStore(defaults)),
            )
        )

        _, kwargs = mock_create.call_args
        assert kwargs["interrupt_on"] == {}
    clear_agent_cache()


@pytest.mark.asyncio
async def test_omitted_interrupt_on_inherits_global_defaults():
    """Absent interrupt_on should continue to inherit default approvals."""
    clear_agent_cache()
    defaults = GlobalAgentDefaults(
        interrupt_on={"execute": {"allowed_decisions": ["approve", "reject"]}}
    )
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                system_prompt="test",
                config_store=cast(Any, _DefaultsOnlyConfigStore(defaults)),
            )
        )

        _, kwargs = mock_create.call_args
        assert kwargs["interrupt_on"] == {
            "execute": {"allowed_decisions": ["approve", "reject"]}
        }
    clear_agent_cache()


@pytest.mark.asyncio
async def test_async_subagents_are_passed_to_deep_agents_subagents():
    """Async subagent specs should use Deep Agents' native subagents input."""
    clear_agent_cache()
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()

        await create_cognition_agent(
            CognitionAgentParams(
                project_path=".",
                model="mock:model",
                async_subagents=[
                    AsyncSubagentConfig(
                        name="researcher",
                        description="Runs long research tasks",
                        graph_id="research_graph",
                        url="https://agents.example.com",
                    )
                ],
            )
        )

        _, kwargs = mock_create.call_args
        assert {
            "name": "researcher",
            "description": "Runs long research tasks",
            "graph_id": "research_graph",
            "url": "https://agents.example.com",
        } in kwargs["subagents"]
    clear_agent_cache()


if __name__ == "__main__":
    pytest.main([__file__])
