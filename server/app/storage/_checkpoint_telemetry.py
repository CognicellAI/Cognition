"""Observe native checkpoint operations without replacing their storage interface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from langgraph.checkpoint.base import BaseCheckpointSaver

from server.app.telemetry import operation

P = ParamSpec("P")
R = TypeVar("R")
Saver = TypeVar("Saver", bound=BaseCheckpointSaver)


def _timed(method: Callable[P, Awaitable[R]], name: str) -> Callable[P, Awaitable[R]]:
    @wraps(method)
    async def measured(*args: P.args, **kwargs: P.kwargs) -> R:
        with operation(name):
            return await method(*args, **kwargs)

    return measured


def observe_checkpointer(saver: Saver) -> Saver:
    """Time this instance's native async I/O; preserve its identity and behavior."""
    if getattr(saver, "_cognition_timed", False):
        return saver
    for method, name in (
        ("aget_tuple", "cognition.checkpoint.load"),
        ("aput", "cognition.checkpoint.save"),
        ("aput_writes", "cognition.checkpoint.write_pending"),
    ):
        setattr(saver, method, _timed(getattr(saver, method), name))
    marker = "_cognition_timed"
    setattr(saver, marker, True)
    return saver
