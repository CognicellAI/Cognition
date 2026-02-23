"""Unit tests for AgentRegistry (P2-9).

Tests for dynamic tool and middleware registration with auto-discovery.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import tool as langchain_tool

from server.app.agent_registry import (
    AgentRegistry,
    get_agent_registry,
    initialize_agent_registry,
    set_agent_registry,
)
from server.app.session_manager import SessionManager
from server.app.settings import Settings


@pytest.fixture
def mock_session_manager():
    """Create a mock session manager."""
    return MagicMock(spec=SessionManager)


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock(spec=Settings)
    settings.workspace_path = Path("/tmp")
    settings.llm_provider = "mock"
    return settings


@pytest.fixture
def agent_registry(mock_session_manager, mock_settings):
    """Create an AgentRegistry with mocks."""
    return AgentRegistry(
        session_manager=mock_session_manager,
        settings=mock_settings,
    )


class TestAgentRegistryToolRegistration:
    """Test tool registration functionality."""

    def test_register_tool(self, agent_registry):
        """Test registering a tool."""

        @langchain_tool
        def test_tool():
            """Test tool."""
            return "result"

        agent_registry.register_tool(
            name="test_tool",
            factory=lambda: test_tool,
            source="programmatic",
        )

        assert "test_tool" in agent_registry._tools
        assert agent_registry._tools["test_tool"].name == "test_tool"

    def test_unregister_tool(self, agent_registry):
        """Test unregistering a tool."""
        agent_registry.register_tool(
            name="test_tool",
            factory=lambda: MagicMock(),
        )

        result = agent_registry.unregister_tool("test_tool")

        assert result is True
        assert "test_tool" not in agent_registry._tools

    def test_unregister_tool_not_found(self, agent_registry):
        """Test unregistering a non-existent tool."""
        result = agent_registry.unregister_tool("non_existent")
        assert result is False

    def test_list_tools(self, agent_registry):
        """Test listing registered tools."""
        agent_registry.register_tool(
            name="tool1",
            factory=lambda: MagicMock(),
        )
        agent_registry.register_tool(
            name="tool2",
            factory=lambda: MagicMock(),
        )

        tools = agent_registry.list_tools()

        assert len(tools) == 2
        tool_names = [t.name for t in tools]
        assert "tool1" in tool_names
        assert "tool2" in tool_names

    def test_get_tool(self, agent_registry):
        """Test getting a specific tool."""
        agent_registry.register_tool(
            name="test_tool",
            factory=lambda: MagicMock(),
        )

        tool = agent_registry.get_tool("test_tool")

        assert tool is not None
        assert tool.name == "test_tool"

    def test_get_tool_not_found(self, agent_registry):
        """Test getting a non-existent tool."""
        tool = agent_registry.get_tool("non_existent")
        assert tool is None

    def test_create_tools(self, agent_registry):
        """Test creating tool instances."""
        mock_tool = MagicMock()
        agent_registry.register_tool(
            name="test_tool",
            factory=lambda: mock_tool,
        )

        tools = agent_registry.create_tools()

        assert len(tools) == 1
        assert tools[0] is mock_tool

    def test_create_tools_with_failure(self, agent_registry):
        """Test that tool creation failures are handled gracefully."""
        agent_registry.register_tool(
            name="good_tool",
            factory=lambda: MagicMock(),
        )
        agent_registry.register_tool(
            name="bad_tool",
            factory=lambda: (_ for _ in ()).throw(Exception("Failed")),
        )

        tools = agent_registry.create_tools()

        # Should only have the good tool
        assert len(tools) == 1


class TestAgentRegistryMiddlewareRegistration:
    """Test middleware registration functionality."""

    def test_register_middleware(self, agent_registry):
        """Test registering middleware."""
        mock_middleware = MagicMock()

        agent_registry.register_middleware(
            name="test_middleware",
            factory=lambda: mock_middleware,
            source="programmatic",
        )

        assert "test_middleware" in agent_registry._middleware
        assert agent_registry.is_middleware_pending() is True

    def test_unregister_middleware(self, agent_registry):
        """Test unregistering middleware."""
        agent_registry.register_middleware(
            name="test_middleware",
            factory=lambda: MagicMock(),
        )

        result = agent_registry.unregister_middleware("test_middleware")

        assert result is True
        assert "test_middleware" not in agent_registry._middleware
        assert agent_registry.is_middleware_pending() is True

    def test_list_middleware(self, agent_registry):
        """Test listing registered middleware."""
        agent_registry.register_middleware(
            name="mw1",
            factory=lambda: MagicMock(),
        )
        agent_registry.register_middleware(
            name="mw2",
            factory=lambda: MagicMock(),
        )

        middlewares = agent_registry.list_middleware()

        assert len(middlewares) == 2

    def test_create_middleware(self, agent_registry):
        """Test creating middleware instances."""
        mock_mw = MagicMock()
        agent_registry.register_middleware(
            name="test_mw",
            factory=lambda: mock_mw,
        )

        middlewares = agent_registry.create_middleware()

        assert len(middlewares) == 1
        assert middlewares[0] is mock_mw

    def test_mark_middleware_reloaded(self, agent_registry):
        """Test marking middleware as reloaded."""
        agent_registry.register_middleware(
            name="test_mw",
            factory=lambda: MagicMock(),
        )
        assert agent_registry.is_middleware_pending() is True

        agent_registry.mark_middleware_reloaded()

        assert agent_registry.is_middleware_pending() is False


class TestAgentRegistryDiscovery:
    """Test auto-discovery functionality."""

    def test_set_tools_path(self, agent_registry):
        """Test setting tools discovery path."""
        agent_registry.set_tools_path("/path/to/tools")

        assert agent_registry._tools_path == Path("/path/to/tools")

    def test_set_middleware_path(self, agent_registry):
        """Test setting middleware discovery path."""
        agent_registry.set_middleware_path("/path/to/middleware")

        assert agent_registry._middleware_path == Path("/path/to/middleware")

    def test_discover_tools_no_path(self, agent_registry):
        """Test discovery when no path is set."""
        count = agent_registry.discover_tools()

        assert count == 0

    def test_discover_middleware_no_path(self, agent_registry):
        """Test discovery when no path is set."""
        count = agent_registry.discover_middleware()

        assert count == 0

    def test_discover_tools_nonexistent_path(self, agent_registry, tmp_path):
        """Test discovery with non-existent path."""
        agent_registry.set_tools_path(tmp_path / "nonexistent")
        count = agent_registry.discover_tools()

        assert count == 0


class TestAgentRegistryReload:
    """Test hot-reload functionality."""

    def test_reload_tools(self, agent_registry):
        """Test reloading tools."""
        # Add a tool
        agent_registry.register_tool(
            name="old_tool",
            factory=lambda: MagicMock(),
            source="file",
        )

        # Reload (will clear file-based tools)
        count = agent_registry.reload_tools()

        # Tool should be gone
        assert "old_tool" not in agent_registry._tools

    def test_reload_middleware(self, agent_registry):
        """Test reloading middleware."""
        # Add middleware
        agent_registry.register_middleware(
            name="old_mw",
            factory=lambda: MagicMock(),
            source="file",
        )

        # Reload (will clear file-based middleware and mark pending)
        count = agent_registry.reload_middleware()

        # Middleware should be gone
        assert "old_mw" not in agent_registry._middleware
        assert agent_registry.is_middleware_pending() is True


class TestAgentRegistryStatus:
    """Test registry status reporting."""

    def test_get_status(self, agent_registry):
        """Test getting registry status."""
        agent_registry.register_tool("tool1", lambda: MagicMock())
        agent_registry.register_middleware("mw1", lambda: MagicMock())
        agent_registry.set_tools_path("/tools")
        agent_registry.set_middleware_path("/middleware")

        status = agent_registry.get_status()

        assert status["tools_registered"] == 1
        assert status["middleware_registered"] == 1
        assert status["middleware_pending"] is True
        assert status["tools_path"] == "/tools"
        assert status["middleware_path"] == "/middleware"


class TestAgentRegistryGlobal:
    """Test global registry functions."""

    def test_get_agent_registry_before_init(self):
        """Test that get_agent_registry raises before initialization."""
        with pytest.raises(RuntimeError, match="Agent registry not initialized"):
            get_agent_registry()

    def test_set_agent_registry(self):
        """Test setting global agent registry."""
        mock_registry = MagicMock(spec=AgentRegistry)
        set_agent_registry(mock_registry)

        assert get_agent_registry() is mock_registry

    def test_initialize_agent_registry(self, mock_session_manager, mock_settings, tmp_path):
        """Test initializing global agent registry."""
        # Create workspace structure
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        cognition = workspace / ".cognition"
        cognition.mkdir()
        (cognition / "tools").mkdir()
        (cognition / "middleware").mkdir()

        mock_settings.workspace_path = workspace

        registry = initialize_agent_registry(mock_session_manager, mock_settings)

        assert registry is not None
        assert get_agent_registry() is registry
        assert registry._tools_path is not None
        assert registry._middleware_path is not None
