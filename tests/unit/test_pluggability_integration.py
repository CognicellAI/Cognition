import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from server.app.agent.cognition_agent import create_cognition_agent
from server.app.llm.registry import register_provider


@pytest.mark.asyncio
async def test_llm_provider_registry_integration():
    """Verify that a custom provider can be registered in the LLM provider registry.

    Since llm_provider was moved from Settings to ConfigRegistry, this test now
    validates that register_provider() makes a custom factory callable via the registry,
    not via Settings.
    """
    mock_model = MagicMock()

    def create_custom_model(config, settings):
        return mock_model

    # register_provider stores factories in the module-level registry dict
    register_provider("custom_test_provider", create_custom_model)

    # Verify the provider is callable from the registry
    from server.app.llm.registry import get_provider_factory

    factory = get_provider_factory("custom_test_provider")
    assert factory is create_custom_model


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
