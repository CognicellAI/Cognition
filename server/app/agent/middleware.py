from __future__ import annotations

import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.callbacks import adispatch_custom_event

from server.app.observability import LLM_CALL_DURATION, TOOL_CALL_COUNT, get_logger

logger = get_logger(__name__)


class CognitionObservabilityMiddleware(AgentMiddleware):
    """Middleware for tracking LLM and tool metrics."""

    @property
    def name(self) -> str:
        return "cognition_observability"

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

    @property
    def name(self) -> str:
        return "cognition_streaming"

    async def abefore_model(self, state: Any, runtime: Any) -> None:
        """Notify client that the agent is thinking."""
        try:
            await adispatch_custom_event(
                "status",
                {"status": "thinking"},
            )
            logger.debug("Dispatched 'thinking' status event")
        except Exception as e:
            logger.debug(f"Failed to dispatch status event: {e}")

    async def aafter_model(self, state: Any, runtime: Any) -> None:
        """Notify client that the agent has finished thinking."""
        try:
            await adispatch_custom_event(
                "status",
                {"status": "idle"},
            )
            logger.debug("Dispatched 'idle' status event")
        except Exception as e:
            logger.debug(f"Failed to dispatch status event: {e}")
