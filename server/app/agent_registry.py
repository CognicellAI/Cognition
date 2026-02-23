"""Agent registry for per-session tool and middleware management.

Enables GUI applications to register tools and middleware that apply
per-session. This works around Deep Agents' compile-once limitation by
managing agent recreation when configurations change.

Layer: 4 (Agent Runtime)

Key features:
- Register tool/middleware factories (fresh instances per session)
- Auto-discovery from .cognition/tools/ and .cognition/middleware/
- Tool hot-reload (immediate for new sessions)
- Middleware session-based reload (existing sessions unchanged)
- Integration with SessionManager for agent lifecycle
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

import structlog
from langchain_core.tools import BaseTool

from server.app.agent.cognition_agent import (
    clear_agent_cache,
    create_cognition_agent,
)
from server.app.session_manager import SessionManager, get_session_manager
from server.app.settings import Settings

logger = structlog.get_logger(__name__)

T = TypeVar("T")

# ============================================================================
# Factory Types
# ============================================================================

ToolFactory = Callable[[], BaseTool]
MiddlewareFactory = Callable[[], Any]


# ============================================================================
# Registration Records
# ============================================================================


@dataclass
class ToolRegistration:
    """Registered tool with metadata."""

    name: str
    factory: ToolFactory
    source: str  # 'programmatic' or file path
    module: str | None = None


@dataclass
class MiddlewareRegistration:
    """Registered middleware with metadata."""

    name: str
    factory: MiddlewareFactory
    source: str  # 'programmatic' or file path


# ============================================================================
# Agent Registry
# ============================================================================


class AgentRegistry:
    """Registry for tools and middleware with per-session management.

    This registry enables GUI applications to:
    1. Register custom tools programmatically
    2. Auto-discover tools from .cognition/tools/ directory
    3. Register custom middleware
    4. Auto-discover middleware from .cognition/middleware/ directory
    5. Manage tool/middleware lifecycle per session

    Key behaviors:
    - Tools: Hot-reload immediately (affects new sessions)
    - Middleware: Session-based reload (new sessions get updates)
    - Agents are recreated via SessionManager when config changes
    """

    def __init__(
        self,
        session_manager: SessionManager | None = None,
        settings: Settings | None = None,
    ):
        """Initialize the agent registry.

        Args:
            session_manager: Optional session manager for agent lifecycle.
                Defaults to global instance.
            settings: Optional settings override.
        """
        self._session_manager = session_manager or get_session_manager()
        self._settings = settings

        # Tool registry
        self._tools: dict[str, ToolRegistration] = {}
        self._tool_modules: dict[str, ModuleType] = {}

        # Middleware registry
        self._middleware: dict[str, MiddlewareRegistration] = {}
        self._middleware_modules: dict[str, ModuleType] = {}

        # Auto-discovery paths
        self._tools_path: Path | None = None
        self._middleware_path: Path | None = None

        # Pending changes flag (for middleware session-based reload)
        self._middleware_pending: bool = False

    # ========================================================================
    # Tool Registration
    # ========================================================================

    def register_tool(
        self,
        name: str,
        factory: ToolFactory,
        source: str = "programmatic",
    ) -> None:
        """Register a tool factory.

        The factory is called to create a fresh tool instance for each
        session. Tools are immediately available for new sessions.

        Args:
            name: Unique tool name.
            factory: Callable that returns a BaseTool instance.
            source: Source of registration ('programmatic' or file path).
        """
        self._tools[name] = ToolRegistration(
            name=name,
            factory=factory,
            source=source,
        )

        # Invalidate agent cache to force recompilation
        clear_agent_cache()

        logger.info(
            "Tool registered",
            tool_name=name,
            source=source,
        )

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool.

        Args:
            name: Tool name to unregister.

        Returns:
            True if tool was removed, False if not found.
        """
        if name in self._tools:
            del self._tools[name]
            clear_agent_cache()
            logger.info("Tool unregistered", tool_name=name)
            return True
        return False

    def list_tools(self) -> list[ToolRegistration]:
        """List all registered tools.

        Returns:
            List of tool registrations.
        """
        return list(self._tools.values())

    def get_tool(self, name: str) -> ToolRegistration | None:
        """Get a tool registration by name.

        Args:
            name: Tool name.

        Returns:
            Tool registration if found, None otherwise.
        """
        return self._tools.get(name)

    def create_tools(self) -> list[BaseTool]:
        """Create tool instances from all registered factories.

        Returns:
            List of instantiated tools.
        """
        tools: list[BaseTool] = []
        for reg in self._tools.values():
            try:
                tool = reg.factory()
                tools.append(tool)
            except Exception as e:
                logger.error(
                    "Failed to create tool",
                    tool_name=reg.name,
                    error=str(e),
                )
        return tools

    # ========================================================================
    # Middleware Registration
    # ========================================================================

    def register_middleware(
        self,
        name: str,
        factory: MiddlewareFactory,
        source: str = "programmatic",
    ) -> None:
        """Register a middleware factory.

        The factory is called to create a fresh middleware instance for
        each session. Middleware changes are applied to new sessions only
        (session-based reload).

        Args:
            name: Unique middleware name.
            factory: Callable that returns a middleware instance.
            source: Source of registration ('programmatic' or file path).
        """
        self._middleware[name] = MiddlewareRegistration(
            name=name,
            factory=factory,
            source=source,
        )

        # Mark middleware as pending (session-based reload)
        self._middleware_pending = True

        logger.info(
            "Middleware registered",
            middleware_name=name,
            source=source,
        )

    def unregister_middleware(self, name: str) -> bool:
        """Unregister middleware.

        Args:
            name: Middleware name to unregister.

        Returns:
            True if middleware was removed, False if not found.
        """
        if name in self._middleware:
            del self._middleware[name]
            self._middleware_pending = True
            logger.info("Middleware unregistered", middleware_name=name)
            return True
        return False

    def list_middleware(self) -> list[MiddlewareRegistration]:
        """List all registered middleware.

        Returns:
            List of middleware registrations.
        """
        return list(self._middleware.values())

    def create_middleware(self) -> list[Any]:
        """Create middleware instances from all registered factories.

        Returns:
            List of instantiated middleware.
        """
        middlewares: list[Any] = []
        for reg in self._middleware.values():
            try:
                middleware = reg.factory()
                middlewares.append(middleware)
            except Exception as e:
                logger.error(
                    "Failed to create middleware",
                    middleware_name=reg.name,
                    error=str(e),
                )
        return middlewares

    def is_middleware_pending(self) -> bool:
        """Check if middleware changes are pending.

        Returns:
            True if middleware has changed and should be reloaded.
        """
        return self._middleware_pending

    def mark_middleware_reloaded(self) -> None:
        """Mark pending middleware changes as applied."""
        self._middleware_pending = False

    # ========================================================================
    # Auto-Discovery from Files
    # ========================================================================

    def set_tools_path(self, path: str | Path) -> None:
        """Set the path for tool auto-discovery.

        Args:
            path: Directory path containing .py tool files.
        """
        self._tools_path = Path(path)

    def set_middleware_path(self, path: str | Path) -> None:
        """Set the path for middleware auto-discovery.

        Args:
            path: Directory path containing .py middleware files.
        """
        self._middleware_path = Path(path)

    def discover_tools(self) -> int:
        """Auto-discover and register tools from the tools path.

        Scans the configured tools directory for Python files and
        registers any functions decorated with @tool.

        Returns:
            Number of tools discovered and registered.
        """
        if not self._tools_path:
            logger.warning("No tools path configured for discovery")
            return 0

        if not self._tools_path.exists():
            logger.debug("Tools path does not exist", path=str(self._tools_path))
            return 0

        count = 0
        for py_file in self._tools_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            try:
                discovered = self._load_tools_from_file(py_file)
                count += discovered
            except Exception as e:
                logger.error(
                    "Failed to load tools from file",
                    file=str(py_file),
                    error=str(e),
                )

        logger.info(
            "Tool discovery complete",
            path=str(self._tools_path),
            tools_discovered=count,
        )
        return count

    def discover_middleware(self) -> int:
        """Auto-discover and register middleware from the middleware path.

        Scans the configured middleware directory for Python files and
        registers any classes inheriting from AgentMiddleware.

        Returns:
            Number of middleware classes discovered and registered.
        """
        if not self._middleware_path:
            logger.warning("No middleware path configured for discovery")
            return 0

        if not self._middleware_path.exists():
            logger.debug("Middleware path does not exist", path=str(self._middleware_path))
            return 0

        count = 0
        for py_file in self._middleware_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            try:
                discovered = self._load_middleware_from_file(py_file)
                count += discovered
            except Exception as e:
                logger.error(
                    "Failed to load middleware from file",
                    file=str(py_file),
                    error=str(e),
                )

        logger.info(
            "Middleware discovery complete",
            path=str(self._middleware_path),
            middleware_discovered=count,
        )
        return count

    def _load_tools_from_file(self, py_file: Path) -> int:
        """Load tools from a Python file.

        Args:
            py_file: Path to the Python file.

        Returns:
            Number of tools loaded.
        """
        module_name = f"_cognition_tools_{py_file.stem}"
        file_path = str(py_file)

        # Remove existing module if already loaded (for hot reload)
        if module_name in sys.modules:
            del sys.modules[module_name]

        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            return 0

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Find @tool decorated functions
        count = 0
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and hasattr(obj, "_tool_decorator"):
                # Register the tool
                self.register_tool(
                    name=name,
                    factory=lambda obj=obj: obj,  # Capture obj in closure
                    source=str(py_file),
                )
                count += 1
            elif isinstance(obj, BaseTool):
                # Already a tool instance
                self.register_tool(
                    name=name,
                    factory=lambda obj=obj: obj,
                    source=str(py_file),
                )
                count += 1

        return count

    def _load_middleware_from_file(self, py_file: Path) -> int:
        """Load middleware from a Python file.

        Args:
            py_file: Path to the Python file.

        Returns:
            Number of middleware classes loaded.
        """
        module_name = f"_cognition_middleware_{py_file.stem}"
        file_path = str(py_file)

        # Remove existing module if already loaded (for hot reload)
        if module_name in sys.modules:
            del sys.modules[module_name]

        # Load the module
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            return 0

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Find AgentMiddleware classes
        from langchain.agents.middleware.types import AgentMiddleware

        count = 0
        for name, obj in inspect.getmembers(module):
            if (
                inspect.isclass(obj)
                and issubclass(obj, AgentMiddleware)
                and obj is not AgentMiddleware
            ):
                # Register the middleware
                self.register_middleware(
                    name=name,
                    factory=lambda obj=obj: obj(),  # Instantiate
                    source=str(py_file),
                )
                count += 1

        return count

    # ========================================================================
    # Agent Creation with Extensions
    # ========================================================================

    async def create_agent_with_extensions(
        self,
        project_path: str | Path,
        session_id: str | None = None,
        model: Any | None = None,
        checkpointer: Any | None = None,
        system_prompt: str | None = None,
        settings: Settings | None = None,
    ) -> Any:
        """Create an agent with all registered extensions.

        This is the primary method for creating agents with custom tools
        and middleware. It combines programmatically registered extensions
        with discovered ones.

        Args:
            project_path: Path to the project workspace.
            session_id: Optional session ID for session-specific agent.
            model: Optional LLM model to use.
            checkpointer: Optional LangGraph checkpointer.
            system_prompt: Optional custom system prompt.
            settings: Optional settings override.

        Returns:
            Compiled Deep Agent with all extensions.
        """
        # Create tools and middleware
        tools = self.create_tools()
        middleware = self.create_middleware()

        # Create the agent
        agent = create_cognition_agent(
            project_path=project_path,
            model=model,
            checkpointer=checkpointer,
            system_prompt=system_prompt,
            tools=tools if tools else None,
            middleware=middleware if middleware else None,
            settings=settings or self._settings,
        )

        # If session_id provided, associate agent with session
        if session_id and self._session_manager:
            managed = self._session_manager._sessions.get(session_id)
            if managed:
                managed.agent = agent

        # Mark middleware as applied
        if self._middleware_pending:
            self.mark_middleware_reloaded()

        logger.info(
            "Agent created with extensions",
            project=str(project_path),
            session_id=session_id,
            tools_count=len(tools),
            middleware_count=len(middleware),
        )

        return agent

    # ========================================================================
    # Hot Reload
    # ========================================================================

    def reload_tools(self) -> int:
        """Hot-reload tools from the discovery path.

        Clears existing file-based tools and re-discovers them.
        This enables immediate updates for GUI development.

        Returns:
            Number of tools reloaded.
        """
        # Remove file-based tools
        to_remove = [name for name, reg in self._tools.items() if reg.source != "programmatic"]
        for name in to_remove:
            del self._tools[name]

        # Re-discover
        count = self.discover_tools()

        # Invalidate agent cache
        clear_agent_cache()

        logger.info("Tools hot-reloaded", tools_reloaded=count)
        return count

    def reload_middleware(self) -> int:
        """Reload middleware from the discovery path.

        Clears existing file-based middleware and re-discovers them.
        This triggers session-based reload (middleware_pending flag).

        Returns:
            Number of middleware reloaded.
        """
        # Remove file-based middleware
        to_remove = [name for name, reg in self._middleware.items() if reg.source != "programmatic"]
        for name in to_remove:
            del self._middleware[name]

        # Re-discover
        count = self.discover_middleware()

        # Mark as pending (session-based reload)
        self._middleware_pending = True

        logger.info("Middleware reloaded", middleware_reloaded=count)
        return count

    # ========================================================================
    # Status
    # ========================================================================

    def get_status(self) -> dict[str, Any]:
        """Get registry status.

        Returns:
            Dict with registration counts and pending changes.
        """
        return {
            "tools_registered": len(self._tools),
            "middleware_registered": len(self._middleware),
            "middleware_pending": self._middleware_pending,
            "tools_path": str(self._tools_path) if self._tools_path else None,
            "middleware_path": str(self._middleware_path) if self._middleware_path else None,
        }


# ============================================================================
# Global Agent Registry
# ============================================================================

_agent_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance.

    Must be initialized by calling initialize_agent_registry() first.

    Returns:
        AgentRegistry instance.

    Raises:
        RuntimeError: If registry has not been initialized.
    """
    if _agent_registry is None:
        raise RuntimeError(
            "Agent registry not initialized. Call initialize_agent_registry() first."
        )
    return _agent_registry


def set_agent_registry(registry: AgentRegistry) -> None:
    """Set the global agent registry instance.

    Args:
        registry: Configured AgentRegistry instance.
    """
    global _agent_registry
    _agent_registry = registry


def initialize_agent_registry(
    session_manager: SessionManager | None = None,
    settings: Settings | None = None,
) -> AgentRegistry:
    """Initialize and return the global agent registry.

    Args:
        session_manager: Optional session manager.
        settings: Optional settings.

    Returns:
        Configured AgentRegistry instance.
    """
    registry = AgentRegistry(session_manager, settings)

    # Set default discovery paths
    if settings and settings.workspace_path:
        tools_path = Path(settings.workspace_path) / ".cognition" / "tools"
        middleware_path = Path(settings.workspace_path) / ".cognition" / "middleware"

        registry.set_tools_path(tools_path)
        registry.set_middleware_path(middleware_path)

        # Auto-discover on initialization
        registry.discover_tools()
        registry.discover_middleware()

    set_agent_registry(registry)
    return registry
