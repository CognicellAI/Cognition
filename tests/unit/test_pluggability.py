from unittest.mock import AsyncMock, patch

import pytest

from server.app.agent.cognition_agent import (
    CognitionAgentParams,
    clear_agent_cache,
    create_cognition_agent,
)


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


if __name__ == "__main__":
    pytest.main([__file__])
