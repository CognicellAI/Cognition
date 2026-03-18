from unittest.mock import AsyncMock, patch

import pytest

from server.app.agent.cognition_agent import create_cognition_agent


@pytest.mark.asyncio
async def test_create_cognition_agent_pluggability():
    """Verify that create_cognition_agent correctly applies pluggability parameters.

    agent_memory/skills/subagents/interrupt_on moved from Settings to direct function
    parameters (resolved from ConfigRegistry at runtime). Callers pass them explicitly.
    """
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()
        await create_cognition_agent(
            project_path=".",
            memory=["TEST_MEMORY.md"],
            skills=[".cognition/skills/"],
            subagents=[{"name": "test-subagent", "system_prompt": "..."}],
            interrupt_on={"execute": True},
        )

        # Verify create_deep_agent was called with correct parameters
        args, kwargs = mock_create.call_args
        assert kwargs["memory"] == ["TEST_MEMORY.md"]
        assert kwargs["skills"] == [".cognition/skills/"]
        # description is normalized to "" when missing (required by deepagents SubAgent spec)
        assert kwargs["subagents"] == [
            {"name": "test-subagent", "system_prompt": "...", "description": ""}
        ]
        assert kwargs["interrupt_on"] == {"execute": True}

        # Verify middleware includes our custom ones
        middleware_names = [m.name for m in kwargs["middleware"]]
        assert "cognition_observability" in middleware_names
        assert "cognition_streaming" in middleware_names


if __name__ == "__main__":
    pytest.main([__file__])
