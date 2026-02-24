from unittest.mock import patch

import pytest

from server.app.agent.cognition_agent import create_cognition_agent
from server.app.settings import get_settings


def test_create_cognition_agent_pluggability():
    """Verify that create_cognition_agent correctly applies pluggability settings."""
    settings = get_settings()

    # Mock settings to provide sample values
    with (
        patch.object(settings, "agent_memory", ["TEST_MEMORY.md"]),
        patch.object(settings, "agent_skills", [".cognition/skills/"]),
        patch.object(
            settings, "agent_subagents", [{"name": "test-subagent", "system_prompt": "..."}]
        ),
        patch.object(settings, "agent_interrupt_on", {"execute": True}),
        patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create,
    ):
        create_cognition_agent(project_path=".")

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
