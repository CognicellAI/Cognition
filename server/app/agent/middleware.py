from __future__ import annotations

import time
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    after_model,
    before_model,
    wrap_model_call,
    wrap_tool_call,
)

from server.app.observability import LLM_CALL_DURATION, TOOL_CALL_COUNT, get_logger

logger = get_logger(__name__)


class CognitionObservabilityMiddleware(AgentMiddleware):
    """Middleware for tracking LLM and tool metrics."""

    name = "cognition_observability"

    @wrap_model_call()
    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Track LLM call duration and usage."""
        start = time.time()
        # Handle different model types gracefully
        provider = "unknown"
        model_name = "unknown"

        if hasattr(request.model, "provider"):
            provider = request.model.provider
        elif hasattr(request.model, "__class__"):
            provider = request.model.__class__.__name__

        if hasattr(request.model, "model_name"):
            model_name = request.model.model_name
        elif hasattr(request.model, "model"):
            model_name = str(request.model.model)

        try:
            response = await handler(request)
            return response
        finally:
            duration = time.time() - start
            LLM_CALL_DURATION.labels(provider=provider, model=model_name).observe(duration)

    @wrap_tool_call()
    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Track tool call frequency and success rate."""
        tool_name = request.tool_call.get("name", "unknown")
        try:
            result = await handler(request)
            TOOL_CALL_COUNT.labels(tool_name=tool_name, status="success").inc()
            return result
        except Exception:
            TOOL_CALL_COUNT.labels(tool_name=tool_name, status="error").inc()
            raise


class CognitionStreamingMiddleware(AgentMiddleware):
    """Middleware for streaming agent status updates to the client."""

    name = "cognition_streaming"

    @before_model()
    async def abefore_model(self, state: Any, runtime: Any) -> None:
        """Notify client that the agent is thinking."""
        if hasattr(runtime, "stream_writer") and runtime.stream_writer:
            try:
                await runtime.stream_writer({"event": "status", "data": {"status": "thinking"}})
            except Exception as e:
                logger.debug(f"Failed to stream status: {e}")

    @after_model()
    async def aafter_model(self, state: Any, runtime: Any) -> None:
        """Notify client that the agent has finished thinking."""
        if hasattr(runtime, "stream_writer") and runtime.stream_writer:
            try:
                await runtime.stream_writer({"event": "status", "data": {"status": "idle"}})
            except Exception as e:
                logger.debug(f"Failed to stream status: {e}")
