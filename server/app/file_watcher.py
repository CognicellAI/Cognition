"""File watcher API for GUI extensibility (P2-10).

Provides hot-reload capabilities for tools, middleware, and configuration files.
Integrates with AgentRegistry to trigger reloads when files change.
"""

from __future__ import annotations

import asyncio
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import structlog
from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

if TYPE_CHECKING:
    from server.app.agent_registry import AgentRegistry

logger = structlog.get_logger(__name__)


@dataclass
class FileWatcherConfig:
    """Configuration for file watching.

    Attributes:
        enabled: Whether file watching is enabled
        debounce_seconds: Seconds to wait before processing changes
        ignore_patterns: Glob patterns for files to ignore
    """

    enabled: bool = True
    debounce_seconds: float = 1.0
    ignore_patterns: list[str] = field(
        default_factory=lambda: [
            "*.pyc",
            "__pycache__",
            ".git",
            ".venv",
            "node_modules",
            ".DS_Store",
        ]
    )


class FileWatcherChangeEvent:
    """Event fired when a watched file changes.

    Attributes:
        event_type: Type of change ('created', 'modified', 'deleted', 'moved')
        src_path: Path to the file that changed
        dest_path: For moved events, the destination path
        is_directory: Whether the change was to a directory
    """

    def __init__(
        self,
        event_type: str,
        src_path: str,
        dest_path: Optional[str] = None,
        is_directory: bool = False,
    ):
        self.event_type = event_type
        self.src_path = src_path
        self.dest_path = dest_path
        self.is_directory = is_directory

    def __repr__(self) -> str:
        return (
            f"FileWatcherChangeEvent("
            f"type={self.event_type}, "
            f"src={self.src_path}, "
            f"dest={self.dest_path}, "
            f"is_dir={self.is_directory})"
        )


class WorkspaceFileHandler(FileSystemEventHandler):
    """File system event handler for workspace files."""

    def __init__(
        self,
        watcher: WorkspaceWatcher,
        path: str,
        watch_type: str,
    ):
        """Initialize handler.

        Args:
            watcher: Parent workspace watcher
            path: Path being watched
            watch_type: Type of watch ('tools', 'middleware', 'config')
        """
        self.watcher = watcher
        self.path = Path(path).resolve()
        self.watch_type = watch_type

    def _should_ignore(self, event_path: str) -> bool:
        """Check if event should be ignored based on patterns."""
        path = Path(event_path)
        for pattern in self.watcher.config.ignore_patterns:
            if fnmatch.fnmatch(path.name, pattern):
                return True
            if any(fnmatch.fnmatch(part, pattern) for part in path.parts):
                return True
        return False

    def _notify_change(
        self, event_type: str, src_path: str, dest_path: Optional[str] = None
    ) -> None:
        """Notify watchers of a change."""
        if self._should_ignore(src_path):
            return

        change_event = FileWatcherChangeEvent(
            event_type=event_type,
            src_path=src_path,
            dest_path=dest_path,
            is_directory=False,
        )

        # Schedule debounced processing
        self.watcher._schedule_change(change_event, self.watch_type)

    def on_created(self, event: FileCreatedEvent | DirCreatedEvent) -> None:
        """Handle file/directory creation."""
        self._notify_change("created", event.src_path)

    def on_modified(self, event: FileModifiedEvent | DirModifiedEvent) -> None:
        """Handle file/directory modification."""
        self._notify_change("modified", event.src_path)

    def on_deleted(self, event: FileDeletedEvent | DirDeletedEvent) -> None:
        """Handle file/directory deletion."""
        self._notify_change("deleted", event.src_path)

    def on_moved(self, event: FileMovedEvent | DirMovedEvent) -> None:
        """Handle file/directory move."""
        self._notify_change("moved", event.src_path, event.dest_path)


class WorkspaceWatcher:
    """File system watcher for workspace changes.

    Watches configuration directories and triggers reloads via AgentRegistry.
    Designed for GUI applications that need to monitor file changes.

    Example:
        ```python
        from server.app.file_watcher import WorkspaceWatcher
        from server.app.agent_registry import AgentRegistry

        registry = AgentRegistry()
        watcher = WorkspaceWatcher(registry)

        # Watch directories
        watcher.watch_tools("/project/.cognition/tools")
        watcher.watch_middleware("/project/.cognition/middleware")
        watcher.watch_config("/project/.cognition/config.yaml")

        # Set up callbacks for GUI
        watcher.on_tools_changed(lambda: gui.notify("Tools reloaded"))
        watcher.on_middleware_changed(lambda: gui.notify("Middleware pending"))

        # Start watching
        watcher.start()

        # Later...
        watcher.stop()
        ```
    """

    def __init__(
        self,
        agent_registry: Optional["AgentRegistry"] = None,
        config: Optional[FileWatcherConfig] = None,
    ):
        """Initialize workspace watcher.

        Args:
            agent_registry: Optional AgentRegistry for triggering reloads
            config: Watcher configuration
        """
        self.agent_registry = agent_registry
        self.config = config or FileWatcherConfig()

        self._observer: Optional[Observer] = None
        self._handlers: dict[str, WorkspaceFileHandler] = {}
        self._debounce_timers: dict[str, asyncio.TimerHandle] = {}

        # Callbacks for GUI notifications
        self._tools_changed_callbacks: list[Callable[[], None]] = []
        self._middleware_changed_callbacks: list[Callable[[], None]] = []
        self._config_changed_callbacks: list[Callable[[], None]] = []

        logger.debug("WorkspaceWatcher initialized", enabled=self.config.enabled)

    def watch_tools(self, tools_dir: str) -> "WorkspaceWatcher":
        """Watch the tools directory for changes.

        When tools change, triggers AgentRegistry.reload_tools() and
        notifies GUI callbacks.

        Args:
            tools_dir: Path to .cognition/tools/ directory

        Returns:
            Self for method chaining
        """
        if not self.config.enabled:
            return self

        path = Path(tools_dir).resolve()
        if not path.exists():
            logger.warning("Tools directory does not exist, skipping watch", path=str(path))
            return self

        handler = WorkspaceFileHandler(self, str(path), "tools")
        self._handlers[str(path)] = handler

        if self._observer:
            self._observer.schedule(handler, str(path), recursive=True)
            logger.info("Watching tools directory", path=str(path))

        return self

    def watch_middleware(self, middleware_dir: str) -> "WorkspaceWatcher":
        """Watch the middleware directory for changes.

        When middleware changes, triggers AgentRegistry.mark_middleware_pending()
        and notifies GUI callbacks.

        Args:
            middleware_dir: Path to .cognition/middleware/ directory

        Returns:
            Self for method chaining
        """
        if not self.config.enabled:
            return self

        path = Path(middleware_dir).resolve()
        if not path.exists():
            logger.warning("Middleware directory does not exist, skipping watch", path=str(path))
            return self

        handler = WorkspaceFileHandler(self, str(path), "middleware")
        self._handlers[str(path)] = handler

        if self._observer:
            self._observer.schedule(handler, str(path), recursive=True)
            logger.info("Watching middleware directory", path=str(path))

        return self

    def watch_config(self, config_path: str) -> "WorkspaceWatcher":
        """Watch the config file for changes.

        When config changes, triggers settings reload and notifies GUI callbacks.

        Args:
            config_path: Path to .cognition/config.yaml file

        Returns:
            Self for method chaining
        """
        if not self.config.enabled:
            return self

        path = Path(config_path).resolve()
        if not path.exists():
            logger.warning("Config file does not exist, skipping watch", path=str(path))
            return self

        handler = WorkspaceFileHandler(self, str(path.parent), "config")
        self._handlers[str(path.parent)] = handler

        if self._observer:
            self._observer.schedule(handler, str(path.parent), recursive=False)
            logger.info("Watching config file", path=str(path))

        return self

    def on_tools_changed(self, callback: Callable[[], None]) -> "WorkspaceWatcher":
        """Register a callback for when tools change.

        Args:
            callback: Function to call when tools are reloaded

        Returns:
            Self for method chaining
        """
        self._tools_changed_callbacks.append(callback)
        return self

    def on_middleware_changed(self, callback: Callable[[], None]) -> "WorkspaceWatcher":
        """Register a callback for when middleware changes.

        Args:
            callback: Function to call when middleware is pending update

        Returns:
            Self for method chaining
        """
        self._middleware_changed_callbacks.append(callback)
        return self

    def on_config_changed(self, callback: Callable[[], None]) -> "WorkspaceWatcher":
        """Register a callback for when config changes.

        Args:
            callback: Function to call when config is reloaded

        Returns:
            Self for method chaining
        """
        self._config_changed_callbacks.append(callback)
        return self

    def start(self) -> None:
        """Start watching for file changes.

        Schedules all registered watches and begins monitoring.
        """
        if not self.config.enabled:
            logger.debug("File watching disabled, not starting")
            return

        if self._observer:
            logger.warning("Watcher already started")
            return

        self._observer = Observer()

        # Schedule all registered handlers
        for path_str, handler in self._handlers.items():
            self._observer.schedule(handler, path_str, recursive=True)

        self._observer.start()
        logger.info("WorkspaceWatcher started", watches=len(self._handlers))

    def stop(self) -> None:
        """Stop watching for file changes.

        Cleans up all resources and cancels pending timers.
        """
        if not self._observer:
            return

        # Cancel all debounce timers
        for timer in self._debounce_timers.values():
            timer.cancel()
        self._debounce_timers.clear()

        self._observer.stop()
        self._observer.join()
        self._observer = None

        logger.info("WorkspaceWatcher stopped")

    def _schedule_change(self, event: FileWatcherChangeEvent, watch_type: str) -> None:
        """Schedule a debounced change processing.

        Args:
            event: The change event
            watch_type: Type of watch ('tools', 'middleware', 'config')
        """
        # Cancel existing timer for this watch type
        key = f"{watch_type}:{event.src_path}"
        if key in self._debounce_timers:
            self._debounce_timers[key].cancel()

        # Create new debounced timer
        loop = asyncio.get_event_loop()
        timer = loop.call_later(
            self.config.debounce_seconds,
            lambda: asyncio.create_task(self._process_change(event, watch_type)),
        )
        self._debounce_timers[key] = timer

        logger.debug(
            "Change scheduled",
            watch_type=watch_type,
            event_type=event.event_type,
            path=event.src_path,
            debounce=self.config.debounce_seconds,
        )

    async def _process_change(self, event: FileWatcherChangeEvent, watch_type: str) -> None:
        """Process a file change event.

        Args:
            event: The change event
            watch_type: Type of watch ('tools', 'middleware', 'config')
        """
        # Remove timer reference
        key = f"{watch_type}:{event.src_path}"
        self._debounce_timers.pop(key, None)

        logger.info(
            "Processing file change",
            watch_type=watch_type,
            event_type=event.event_type,
            path=event.src_path,
        )

        try:
            if watch_type == "tools" and self.agent_registry:
                # Reload tools (immediate for new sessions)
                self.agent_registry.reload_tools()
                logger.info("Tools reloaded")

                # Notify GUI callbacks
                for callback in self._tools_changed_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback()
                        else:
                            callback()
                    except Exception as e:
                        logger.error("Tools changed callback failed", error=str(e))

            elif watch_type == "middleware" and self.agent_registry:
                # Mark middleware pending (session-based reload)
                self.agent_registry.mark_middleware_pending()
                logger.info("Middleware marked pending")

                # Notify GUI callbacks
                for callback in self._middleware_changed_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback()
                        else:
                            callback()
                    except Exception as e:
                        logger.error("Middleware changed callback failed", error=str(e))

            elif watch_type == "config":
                # Trigger config reload
                logger.info("Config changed, reload triggered")

                # Notify GUI callbacks
                for callback in self._config_changed_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback()
                        else:
                            callback()
                    except Exception as e:
                        logger.error("Config changed callback failed", error=str(e))

        except Exception as e:
            logger.error(
                "Failed to process file change",
                watch_type=watch_type,
                error=str(e),
            )

    def __enter__(self) -> "WorkspaceWatcher":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()


class SimpleFileWatcher:
    """Simple file watcher for basic use cases.

    This is a simplified API for watching a single directory or file
    without the full AgentRegistry integration.

    Example:
        ```python
        from server.app.file_watcher import SimpleFileWatcher

        def on_change(event):
            print(f"File {event.src_path} was {event.event_type}")

        watcher = SimpleFileWatcher("/path/to/watch", on_change)
        watcher.start()

        # Later...
        watcher.stop()
        ```
    """

    def __init__(
        self,
        path: str,
        callback: Callable[[FileWatcherChangeEvent], None],
        recursive: bool = True,
    ):
        """Initialize simple file watcher.

        Args:
            path: Directory or file to watch
            callback: Function to call when changes occur
            recursive: Whether to watch subdirectories
        """
        self.path = Path(path).resolve()
        self.callback = callback
        self.recursive = recursive
        self._observer: Optional[Observer] = None
        self._handler: Optional[_SimpleFileHandler] = None

    def start(self) -> None:
        """Start watching."""
        if not self.path.exists():
            raise FileNotFoundError(f"Path does not exist: {self.path}")

        self._observer = Observer()
        self._handler = _SimpleFileHandler(self.callback)
        self._observer.schedule(self._handler, str(self.path), recursive=self.recursive)
        self._observer.start()

        logger.info(
            "SimpleFileWatcher started",
            path=str(self.path),
            recursive=self.recursive,
        )

    def stop(self) -> None:
        """Stop watching."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("SimpleFileWatcher stopped")

    def __enter__(self) -> "SimpleFileWatcher":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()


class _SimpleFileHandler(FileSystemEventHandler):
    """Internal handler for SimpleFileWatcher."""

    def __init__(self, callback: Callable[[FileWatcherChangeEvent], None]):
        self.callback = callback

    def on_any_event(self, event) -> None:
        """Handle any file system event."""
        change_event = FileWatcherChangeEvent(
            event_type=event.event_type,
            src_path=event.src_path,
            dest_path=getattr(event, "dest_path", None),
            is_directory=event.is_directory,
        )
        self.callback(change_event)
