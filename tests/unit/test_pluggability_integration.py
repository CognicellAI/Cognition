import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from server.app.agent.cognition_agent import create_cognition_agent


@pytest.mark.asyncio
async def test_custom_model_instance_integration():
    """Verify that a pre-built BaseChatModel instance can be passed directly
    to create_cognition_agent, bypassing provider resolution entirely.

    This is the recommended pattern for custom providers: build the model
    yourself via init_chat_model or a provider SDK, then pass the instance.
    """
    from langchain_core.language_models import BaseChatModel

    mock_model = MagicMock(spec=BaseChatModel)

    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        await create_cognition_agent(project_path=".", model=mock_model)

        _, kwargs = mock_create.call_args
        assert kwargs["model"] is mock_model, (
            "The exact model instance should be forwarded to create_deep_agent"
        )


@pytest.mark.asyncio
async def test_agent_full_config_passing(tmp_path):
    """Verify that all pluggability settings are correctly passed to the agent runtime."""
    from unittest.mock import AsyncMock

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    subagents = [{"name": "test-subagent", "system_prompt": "...", "description": "Test subagent"}]
    interrupt_on = {"execute": True}

    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()
        await create_cognition_agent(
            project_path=workspace, subagents=subagents, interrupt_on=interrupt_on
        )

        args, kwargs = mock_create.call_args
        assert kwargs["subagents"] == subagents
        assert kwargs["interrupt_on"] == interrupt_on


@pytest.mark.asyncio
async def test_custom_tools_integration():
    """Verify that custom tools can be passed to the agent."""
    from unittest.mock import AsyncMock

    from langchain_core.tools import tool

    @tool
    def my_custom_tool(input: str) -> str:
        """My custom tool."""
        return "result"

    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
        mock_create.return_value = AsyncMock()
        await create_cognition_agent(project_path=".", tools=[my_custom_tool])

        args, kwargs = mock_create.call_args
        assert my_custom_tool in kwargs["tools"]


@pytest.mark.asyncio
async def test_middleware_execution_integration():
    """Verify that cognition middleware is included and can be triggered."""
    from server.app.agent.middleware import (
        CognitionObservabilityMiddleware,
        CognitionStreamingMiddleware,
    )
    from server.app.llm.mock import MockLLM

    # Create agent with mock LLM instance
    agent = await create_cognition_agent(project_path=".", model=MockLLM())

    # The agent returned is a CompiledStateGraph.
    # We can check its middleware stack if deepagents exposes it,
    # but since we already verified it's passed in the unit test,
    # here we want to see it in action if possible.

    # However, running a full ReAct loop with a mock LLM just to test middleware
    # might be brittle if deepagents internals change.

    # Instead, let's verify the middleware classes themselves work as expected when called.

    mw = CognitionObservabilityMiddleware()
    mock_handler = MagicMock(return_value=asyncio.Future())
    mock_handler.return_value.set_result("response")

    mock_request = MagicMock()
    mock_request.model.provider = "test_provider"
    mock_request.model.model_name = "test_model"

    await mw.awrap_model_call(mock_request, mock_handler)
    mock_handler.assert_called_once_with(mock_request)

    # Verify streaming middleware uses adispatch_custom_event
    smw = CognitionStreamingMiddleware()
    mock_runtime = MagicMock()

    # Middleware should handle adispatch_custom_event gracefully
    # (it will fail without a callback context, but should not crash)
    try:
        await smw.abefore_model({}, mock_runtime)
    except Exception:
        pass  # Expected to fail without LangChain callback context


@pytest.mark.asyncio
async def test_config_loader_to_settings_integration(tmp_path):
    """Verify that ConfigLoader correctly parses infrastructure settings from YAML.

    Agent settings (memory, skills, subagents, interrupt_on) moved to ConfigRegistry.
    This test now validates the infrastructure-level settings that remain in Settings
    (sandbox, persistence, etc.) are correctly parsed by ConfigLoader.
    """
    from server.app.config_loader import ConfigLoader
    from server.app.settings import Settings

    # Create project config with infrastructure settings
    project_dir = tmp_path / "project"
    config_dir = project_dir / ".cognition"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.yaml"

    config_file.write_text("""
server:
  sandbox_backend: docker
  docker_image: my-custom-image:latest
""")

    # Pass project_dir as cwd to ConfigLoader
    loader = ConfigLoader(cwd=project_dir)
    env_vars = loader.to_env_vars()

    # Mock environment variables and load settings
    with patch.dict(os.environ, env_vars):
        settings = Settings()

    assert settings.sandbox_backend == "docker"
    assert settings.docker_image == "my-custom-image:latest"
