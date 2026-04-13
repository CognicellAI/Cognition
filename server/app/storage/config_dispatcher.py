"""ConfigChangeDispatcher — hot-reload invalidation bus.

When a config entity is written via the ConfigRegistry, the dispatcher
notifies all registered subscribers so they can invalidate their caches
(agent graph, LLM model, etc.) without a server restart.

Two implementations:

InProcessDispatcher (SQLite / single-instance)
    All writes happen in the same process, so we dispatch synchronously
    inside the write call — zero latency, no polling needed. Used by
    SqliteConfigRegistry.

PostgresListenDispatcher (Postgres / multi-instance)
    Subscribes to PostgreSQL NOTIFY on channel "cognition_config_changes".
    Each write in PostgresConfigRegistry executes NOTIFY after the INSERT
    into config_changes. The dispatcher maintains a persistent asyncpg
    connection and pushes the change to all subscribers.

Usage:

    # Create
    dispatcher = InProcessDispatcher()

    # Register subscribers
    dispatcher.subscribe(my_handler)

    # Start (no-op for InProcess; starts LISTEN loop for Postgres)
    await dispatcher.start()

    # Emit from within the registry write path
    dispatcher.emit_sync(ConfigChangeEvent(...))   # InProcess
    await dispatcher.emit(ConfigChangeEvent(...))  # either

    # Stop on shutdown
    await dispatcher.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from server.app.storage.config_models import ConfigChangeEvent
from server.app.storage.config_registry import _scope_from_json

logger = logging.getLogger(__name__)

Subscriber = Callable[[ConfigChangeEvent], Awaitable[None]]


@runtime_checkable
class ConfigChangeDispatcher(Protocol):
    """Async publish/subscribe bus for config change events."""

    def subscribe(self, handler: Subscriber) -> None:
        """Register a coroutine function to receive ConfigChangeEvents."""
        ...

    def unsubscribe(self, handler: Subscriber) -> None:
        """Remove a previously registered handler."""
        ...

    async def emit(self, event: ConfigChangeEvent) -> None:
        """Publish an event to all subscribers."""
        ...

    async def start(self) -> None:
        """Start the dispatcher (e.g. establish LISTEN connection)."""
        ...

    async def stop(self) -> None:
        """Stop the dispatcher and release resources."""
        ...


# ---------------------------------------------------------------------------
# InProcessDispatcher
# ---------------------------------------------------------------------------


class InProcessDispatcher:
    """Synchronous in-process dispatcher for SQLite / single-instance use.

    Dispatches are delivered to subscribers immediately within the same
    event-loop turn as the write. This means cache invalidation is complete
    before the API response is returned to the caller.

    Thread-safety: designed for single-threaded asyncio use.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, handler: Subscriber) -> None:
        """Register a subscriber."""
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        """Remove a subscriber."""
        try:
            self._subscribers.remove(handler)
        except ValueError:
            pass

    async def emit(self, event: ConfigChangeEvent) -> None:
        """Deliver event to all subscribers, awaiting each in sequence."""
        for handler in list(self._subscribers):
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "ConfigChangeDispatcher subscriber raised",
                    extra={"handler": getattr(handler, "__name__", repr(handler))},
                )

    def emit_sync(self, event: ConfigChangeEvent) -> None:
        """Fire-and-forget: schedule emit on the running event loop.

        Useful when called from non-async write helpers that already hold
        a DB connection (the asyncio task is created after the caller's
        coroutine yields control).
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.emit(event))
        except RuntimeError:
            pass  # No event loop — tests or startup context

    async def start(self) -> None:
        """No-op for in-process dispatcher."""

    async def stop(self) -> None:
        """No-op for in-process dispatcher."""


# ---------------------------------------------------------------------------
# PostgresListenDispatcher
# ---------------------------------------------------------------------------


class PostgresListenDispatcher:
    """Cross-instance invalidation via PostgreSQL LISTEN/NOTIFY.

    Maintains a persistent asyncpg connection subscribed to
    "cognition_config_changes". When a NOTIFY arrives, it queries
    `config_changes` for unprocessed rows since the last poll and
    dispatches a ConfigChangeEvent for each.

    The connection is kept alive with a periodic ping to survive
    idle-connection timeouts on proxied Postgres deployments (e.g. RDS).

    Args:
        dsn: asyncpg-compatible DSN string.
        poll_batch_size: Max rows to process per NOTIFY wake-up.
        keepalive_interval: Seconds between ping queries (default 30).
    """

    def __init__(
        self,
        dsn: str,
        poll_batch_size: int = 100,
        keepalive_interval: float = 30.0,
    ) -> None:
        self._dsn = dsn
        self._poll_batch_size = poll_batch_size
        self._keepalive_interval = keepalive_interval
        self._subscribers: list[Subscriber] = []
        self._conn: Any = None  # asyncpg.Connection
        self._listen_task: asyncio.Task[None] | None = None
        self._running = False

    def subscribe(self, handler: Subscriber) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        try:
            self._subscribers.remove(handler)
        except ValueError:
            pass

    async def emit(self, event: ConfigChangeEvent) -> None:
        """Deliver event to all subscribers."""
        for handler in list(self._subscribers):
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "PostgresListenDispatcher subscriber raised",
                    extra={"handler": getattr(handler, "__name__", repr(handler))},
                )

    async def start(self) -> None:
        """Connect to Postgres and start LISTEN loop."""
        import asyncpg

        self._running = True
        self._conn = await asyncpg.connect(self._dsn)
        # asyncpg does not auto-decode JSONB/JSON — register codecs so that
        # the scope column (jsonb) comes back as a Python dict, not a string.
        await self._conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        await self._conn.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )
        await self._conn.add_listener("cognition_config_changes", self._on_notify)
        self._listen_task = asyncio.create_task(self._keepalive_loop())
        logger.info("PostgresListenDispatcher started LISTEN on cognition_config_changes")

    async def stop(self) -> None:
        """Stop the LISTEN loop and close connection."""
        self._running = False
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._conn is not None:
            try:
                await self._conn.remove_listener("cognition_config_changes", self._on_notify)
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
        logger.info("PostgresListenDispatcher stopped")

    def _on_notify(
        self,
        conn: Any,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        """Called by asyncpg when a NOTIFY arrives. Schedule async processing."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._process_pending())
        except RuntimeError:
            pass

    async def _process_pending(self) -> None:
        """Query config_changes for unprocessed rows and dispatch events."""
        if self._conn is None:
            return
        try:
            rows = await self._conn.fetch(
                """
                SELECT id, entity_type, name, scope, operation
                FROM config_changes
                WHERE processed = false
                ORDER BY changed_at ASC
                LIMIT $1
                """,
                self._poll_batch_size,
            )
            if not rows:
                return

            change_ids = [row["id"] for row in rows]

            for row in rows:
                event = ConfigChangeEvent(
                    entity_type=row["entity_type"],
                    name=row["name"],
                    scope=_scope_from_json(row["scope"]),
                    operation=row["operation"],
                )
                await self.emit(event)

            # Mark rows processed
            await self._conn.execute(
                "UPDATE config_changes SET processed = true WHERE id = ANY($1::int[])",
                change_ids,
            )
        except Exception:
            logger.exception("PostgresListenDispatcher._process_pending failed")

    async def _keepalive_loop(self) -> None:
        """Periodically ping Postgres to keep the connection alive."""
        while self._running:
            try:
                await asyncio.sleep(self._keepalive_interval)
                if self._conn is not None:
                    await self._conn.execute("SELECT 1")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("PostgresListenDispatcher keepalive ping failed")


# ---------------------------------------------------------------------------
# No-op dispatcher (for tests)
# ---------------------------------------------------------------------------


class NoopDispatcher:
    """Dispatcher that records events but does nothing else. Useful in tests."""

    def __init__(self) -> None:
        self.events: list[ConfigChangeEvent] = []
        self._subscribers: list[Subscriber] = []

    def subscribe(self, handler: Subscriber) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Subscriber) -> None:
        try:
            self._subscribers.remove(handler)
        except ValueError:
            pass

    async def emit(self, event: ConfigChangeEvent) -> None:
        self.events.append(event)
        for handler in list(self._subscribers):
            try:
                await handler(event)
            except Exception:
                pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


__all__ = [
    "ConfigChangeDispatcher",
    "InProcessDispatcher",
    "NoopDispatcher",
    "PostgresListenDispatcher",
    "Subscriber",
]
