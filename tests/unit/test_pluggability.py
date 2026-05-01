from unittest.mock import AsyncMock, patch

import pytest

from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent


@pytest.mark.asyncio
async def test_create_cognition_agent_pluggability():
    """Verify that create_cognition_agent correctly applies pluggability parameters."""
    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()
        params = CognitionAgentParams(
            project_path=".",
            memory=["TEST_MEMORY.md"],
            skills=["clean-code"],
            subagents=[{"name": "test-subagent", "system_prompt": "..."}],
            interrupt_on={"execute": True},
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
        assert kwargs["interrupt_on"] == {"execute": True}

        middleware_names = [m.name for m in kwargs["middleware"]]
        assert "cognition_observability" in middleware_names
        assert "cognition_streaming" in middleware_names


if __name__ == "__main__":
    pytest.main([__file__])
