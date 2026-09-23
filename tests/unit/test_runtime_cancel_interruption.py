"""Cancellation interrupts a silent graph step before it produces effects."""
import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from server.app.agent.runtime import DeepAgentRuntime, ErrorEvent


@pytest.mark.asyncio
async def test_abort_interrupts_silent_step_before_side_effect() -> None:
    started = asyncio.Event()
    released = asyncio.Event()
    stopped = asyncio.Event()
    effects: list[str] = []

    class Graph:
        async def astream(self, *args: Any, **kwargs: Any) -> Any:
            started.set()
            try:
                await released.wait()
                effects.append("too late")
                yield {"type": "custom", "data": {}, "ns": ()}
            finally:
                stopped.set()

    runtime = DeepAgentRuntime(Graph(), MemorySaver(), thread_id="target")

    async def consume() -> list[Any]:
        return [event async for event in runtime.astream_events("work")]

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 1)
    await runtime.abort("unrelated")
    assert not consumer.done()
    await runtime.abort("target")
    events = await asyncio.wait_for(consumer, 1)
    assert stopped.is_set()
    assert effects == []
    assert any(isinstance(event, ErrorEvent) and event.code == "ABORTED" for event in events)
