"""Unit tests for ConfigChangeDispatcher implementations.

Covers:
- InProcessDispatcher: subscribe/unsubscribe, emit, start/stop
- Protocol conformance
"""

from __future__ import annotations

import asyncio

import pytest

from server.app.storage.config_dispatcher import InProcessDispatcher
from server.app.storage.config_models import ConfigChangeEvent


def _event(name: str = "myskill") -> ConfigChangeEvent:
    return ConfigChangeEvent(entity_type="skill", name=name, scope={}, operation="upsert")


# ---------------------------------------------------------------------------
# InProcessDispatcher
# ---------------------------------------------------------------------------


class TestInProcessDispatcher:
    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self):
        """Subscribed handler should receive emitted events."""
        dispatcher = InProcessDispatcher()
        received: list[ConfigChangeEvent] = []

        async def handler(event: ConfigChangeEvent) -> None:
            received.append(event)

        dispatcher.subscribe(handler)
        await dispatcher.emit(_event("skill-1"))

        assert len(received) == 1
        assert received[0].name == "skill-1"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        """All subscribers receive each emitted event."""
        dispatcher = InProcessDispatcher()
        a_received: list[ConfigChangeEvent] = []
        b_received: list[ConfigChangeEvent] = []

        async def handler_a(event: ConfigChangeEvent) -> None:
            a_received.append(event)

        async def handler_b(event: ConfigChangeEvent) -> None:
            b_received.append(event)

        dispatcher.subscribe(handler_a)
        dispatcher.subscribe(handler_b)
        await dispatcher.emit(_event())

        assert len(a_received) == 1
        assert len(b_received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self):
        """Unsubscribed handler should not receive subsequent events."""
        dispatcher = InProcessDispatcher()
        received: list[ConfigChangeEvent] = []

        async def handler(event: ConfigChangeEvent) -> None:
            received.append(event)

        dispatcher.subscribe(handler)
        await dispatcher.emit(_event("before"))
        dispatcher.unsubscribe(handler)
        await dispatcher.emit(_event("after"))

        assert len(received) == 1
        assert received[0].name == "before"

    @pytest.mark.asyncio
    async def test_emit_with_no_subscribers(self):
        """Emitting with no subscribers should not raise."""
        dispatcher = InProcessDispatcher()
        await dispatcher.emit(_event())  # Should not raise

    @pytest.mark.asyncio
    async def test_start_and_stop_are_noops(self):
        """InProcessDispatcher.start/stop should succeed without side effects."""
        dispatcher = InProcessDispatcher()
        await dispatcher.start()
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_emit_sync(self):
        """emit_sync should synchronously schedule delivery in the event loop."""
        dispatcher = InProcessDispatcher()
        received: list[ConfigChangeEvent] = []

        async def handler(event: ConfigChangeEvent) -> None:
            received.append(event)

        dispatcher.subscribe(handler)
        dispatcher.emit_sync(_event("sync-skill"))
        # Allow event loop to process scheduled coroutines
        await asyncio.sleep(0)

        assert len(received) == 1
        assert received[0].name == "sync-skill"

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_block_other_handlers(self):
        """A failing handler must not prevent other handlers from receiving the event."""
        dispatcher = InProcessDispatcher()
        good_received: list[ConfigChangeEvent] = []

        async def bad_handler(event: ConfigChangeEvent) -> None:
            raise RuntimeError("boom")

        async def good_handler(event: ConfigChangeEvent) -> None:
            good_received.append(event)

        dispatcher.subscribe(bad_handler)
        dispatcher.subscribe(good_handler)
        await dispatcher.emit(_event())

        assert len(good_received) == 1

    def test_duplicate_subscribe_is_idempotent(self):
        """Subscribing the same handler twice should not double-deliver events."""
        dispatcher = InProcessDispatcher()
        received: list[ConfigChangeEvent] = []

        async def handler(event: ConfigChangeEvent) -> None:
            received.append(event)

        dispatcher.subscribe(handler)
        dispatcher.subscribe(handler)  # duplicate

        # Verify internally it's only registered once
        assert dispatcher._subscribers.count(handler) == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestDispatcherProtocol:
    def test_in_process_satisfies_protocol(self):
        from server.app.storage.config_dispatcher import ConfigChangeDispatcher

        dispatcher = InProcessDispatcher()
        assert isinstance(dispatcher, ConfigChangeDispatcher)
