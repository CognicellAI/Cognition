from __future__ import annotations

import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.callbacks import adispatch_custom_event
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from server.app.observability import (
    LLM_CALL_DURATION,
    TOOL_CALL_COUNT,
    TOOL_SAFETY_EVENT_COUNT,
    get_logger,
)
from server.app.observability import span as trace_span

logger = get_logger(__name__)


_TRUSTED_CONTEXT_ARG_NAMES = {
    "session_id",
    "thread_id",
    "run_id",
    "agent_name",
    "effective_scope",
    "scope",
    "metadata",
}


def _runtime_config(runtime: Any) -> dict[str, Any]:
    config = getattr(runtime, "config", None)
    return config if isinstance(config, dict) else {}


def _runtime_context(runtime: Any) -> Any:
    return getattr(runtime, "context", None)


def _context_value(context: Any, name: str) -> Any:
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get(name)
    return getattr(context, name, None)


def _trusted_context_values(runtime: Any) -> dict[str, Any]:
    context = _runtime_context(runtime)
    config = _runtime_config(runtime)
    raw_configurable = config.get("configurable")
    configurable: dict[str, Any] = raw_configurable if isinstance(raw_configurable, dict) else {}

    effective_scope = _context_value(context, "effective_scope") or {}
    metadata = _context_value(context, "metadata") or {}
    thread_id = _context_value(context, "thread_id") or configurable.get("thread_id")

    return {
        "session_id": _context_value(context, "session_id") or configurable.get("session_id"),
        "thread_id": thread_id,
        "run_id": config.get("run_id") or configurable.get("run_id") or thread_id,
        "agent_name": _context_value(context, "agent_name") or configurable.get("agent_name"),
        "effective_scope": dict(effective_scope),
        "scope": dict(effective_scope),
        "metadata": dict(metadata),
    }


def _safe_tool_event_context(runtime: Any) -> dict[str, Any]:
    trusted_values = _trusted_context_values(runtime)
    effective_scope = trusted_values.get("effective_scope")
    return {
        "session_id": trusted_values.get("session_id"),
        "run_id": trusted_values.get("run_id"),
        "scope_keys": sorted(effective_scope) if isinstance(effective_scope, dict) else [],
    }


def _tool_safety_attributes(
    *,
    action: str,
    tool_name: str,
    tool_call_id: str | None,
    safe_context: dict[str, Any],
    fields: list[str] | None = None,
    overwritten_fields: list[str] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scope_keys = safe_context.get("scope_keys")
    return {
        "cognition.tool_safety.action": action,
        "tool.name": tool_name,
        "tool.call_id": tool_call_id or "",
        "cognition.session_id": str(safe_context.get("session_id") or ""),
        "cognition.run_id": str(safe_context.get("run_id") or ""),
        "cognition.scope_keys": ",".join(scope_keys) if isinstance(scope_keys, list) else "",
        "cognition.tool_safety.fields": ",".join(fields or []),
        "cognition.tool_safety.overwritten_fields": ",".join(overwritten_fields or []),
        "cognition.tool_safety.error_count": len(errors or []),
    }


def _audit_tool_safety(
    *,
    action: str,
    tool_name: str,
    tool_call_id: str | None,
    safe_context: dict[str, Any],
    fields: list[str] | None = None,
    overwritten_fields: list[str] | None = None,
    errors: list[dict[str, Any]] | None = None,
    message: str | None = None,
) -> None:
    log_payload = {
        "action": action,
        "tool": tool_name,
        "tool_call_id": tool_call_id,
        "fields": fields or [],
        "overwritten_fields": overwritten_fields or [],
        "errors": errors or [],
        "message": message,
        **safe_context,
    }
    if action in {"argument_validation_failed", "blocked"}:
        logger.warning("tool_safety", **log_payload)
    else:
        logger.info("tool_safety", **log_payload)

    TOOL_SAFETY_EVENT_COUNT.labels(action=action, tool_name=tool_name).inc()

    with trace_span(
        "cognition.tool_safety",
        _tool_safety_attributes(
            action=action,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            safe_context=safe_context,
            fields=fields,
            overwritten_fields=overwritten_fields,
            errors=errors,
        ),
    ):
        pass


def _tool_schema_field_names(tool: Any) -> set[str]:
    if tool is None or not hasattr(tool, "get_input_schema"):
        return set()
    try:
        schema = tool.get_input_schema()
    except Exception:
        return set()
    fields = getattr(schema, "model_fields", None)
    if isinstance(fields, dict):
        return set(fields)
    return set()


class TrustedRuntimeContextMiddleware(AgentMiddleware):
    """Inject trusted Cognition runtime context into tool calls.

    Builders can access the full hidden context by declaring
    ``runtime: ToolRuntime`` on a tool. For tools that deliberately expose
    reserved context fields such as ``session_id`` or ``effective_scope`` as
    normal arguments, this middleware fills those fields from runtime context
    and overwrites any model-provided values.
    """

    @property
    def name(self) -> str:
        return "cognition_trusted_runtime_context"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = getattr(request, "tool_call", {})
        if not isinstance(tool_call, dict):
            return await handler(request)

        args = tool_call.get("args")
        if not isinstance(args, dict):
            return await handler(request)

        trusted_values = _trusted_context_values(getattr(request, "runtime", None))
        schema_fields = _tool_schema_field_names(getattr(request, "tool", None))
        target_fields = (
            set(args).intersection(_TRUSTED_CONTEXT_ARG_NAMES)
            | schema_fields.intersection(_TRUSTED_CONTEXT_ARG_NAMES)
        )
        if not target_fields:
            return await handler(request)

        injected = {
            field: trusted_values[field]
            for field in sorted(target_fields)
            if field in trusted_values and trusted_values[field] is not None
        }
        if not injected:
            return await handler(request)

        original_model_fields = sorted(set(args).intersection(injected))
        sanitized_call = {
            **tool_call,
            "args": {
                **args,
                **injected,
            },
        }

        tool_name = tool_call.get("name", "unknown")
        tool_call_id = tool_call.get("id")
        fields = sorted(injected)
        logger.info(
            "tool_context_injected",
            tool=tool_name,
            tool_call_id=tool_call_id,
            fields=fields,
            overwritten_fields=original_model_fields,
        )
        safe_context = _safe_tool_event_context(getattr(request, "runtime", None))
        _audit_tool_safety(
            action="context_injected",
            tool_name=str(tool_name),
            tool_call_id=str(tool_call_id) if tool_call_id is not None else None,
            safe_context=safe_context,
            fields=fields,
            overwritten_fields=original_model_fields,
        )
        try:
            await adispatch_custom_event(
                "tool_context_injected",
                {
                    "event": "tool_context_injected",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "fields": fields,
                    "overwritten_fields": original_model_fields,
                    **safe_context,
                },
            )
        except Exception as e:
            logger.debug("Failed to dispatch tool context event", error=str(e))

        return await handler(request.override(tool_call=sanitized_call))


class ToolArgumentValidationMiddleware(AgentMiddleware):
    """Validate tool arguments before execution and emit repair signals."""

    @property
    def name(self) -> str:
        return "cognition_tool_argument_validation"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = getattr(request, "tool_call", {})
        tool = getattr(request, "tool", None)
        if not isinstance(tool_call, dict) or tool is None:
            return await handler(request)

        args = tool_call.get("args")
        if not isinstance(args, dict) or not hasattr(tool, "get_input_schema"):
            return await handler(request)

        try:
            schema = tool.get_input_schema()
            schema.model_validate(args)
        except ValidationError as exc:
            tool_name = tool_call.get("name", "unknown")
            tool_call_id = tool_call.get("id", "unknown")
            errors = [
                {
                    "loc": list(error.get("loc", ())),
                    "msg": error.get("msg", "Invalid value"),
                    "type": error.get("type", "validation_error"),
                }
                for error in exc.errors()
            ]
            logger.warning(
                "tool_argument_validation_failed",
                tool=tool_name,
                tool_call_id=tool_call_id,
                errors=errors,
            )
            safe_context = _safe_tool_event_context(getattr(request, "runtime", None))
            _audit_tool_safety(
                action="argument_validation_failed",
                tool_name=str(tool_name),
                tool_call_id=str(tool_call_id),
                safe_context=safe_context,
                errors=errors,
                message="Tool argument validation failed",
            )
            try:
                await adispatch_custom_event(
                    "tool_argument_validation_failed",
                    {
                        "event": "tool_argument_validation_failed",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "errors": errors,
                        **safe_context,
                    },
                )
            except Exception as event_exc:
                logger.debug(
                    "Failed to dispatch tool argument validation event",
                    error=str(event_exc),
                )
            return ToolMessage(
                content=(
                    "Tool argument validation failed. Retry the tool call with "
                    f"arguments matching the schema. Errors: {errors}"
                ),
                tool_call_id=tool_call_id,
                name=tool_name,
                status="error",
            )
        except Exception:
            return await handler(request)

        return await handler(request)


class ToolSecurityMiddleware(AgentMiddleware):
    """Middleware for runtime tool security auditing and blocking.

    This middleware intercepts every tool call and:
    - Logs tool invocations to the structured log pipeline
    - Blocks tools on a configurable blocklist

    The blocked_tools list is sourced from Settings.blocked_tools.
    """

    def __init__(self, blocked_tools: list[str] | None = None) -> None:
        """Initialize the middleware.

        Args:
            blocked_tools: List of tool names to block. If None, uses settings.
        """
        self._blocked_tools = set(blocked_tools or [])

    @property
    def name(self) -> str:
        return "cognition_tool_security"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Intercept tool calls for security auditing and blocking.

        Args:
            request: The tool call request.
            handler: The next handler in the chain.

        Returns:
            Tool result or error message if blocked.
        """
        tool_name = (
            request.tool_call.get("name", "unknown") if hasattr(request, "tool_call") else "unknown"
        )
        session_id = (
            getattr(request.runtime.config, "configurable", {}).get("thread_id")
            if hasattr(request, "runtime")
            else None
        )

        logger.info(
            "tool_call",
            tool=tool_name,
            session_id=session_id,
        )

        if tool_name in self._blocked_tools:
            logger.warning("tool_blocked", tool=tool_name, session_id=session_id)
            tool_call_id = (
                request.tool_call.get("id")
                if hasattr(request, "tool_call") and isinstance(request.tool_call, dict)
                else "unknown"
            )
            message = f"Tool '{tool_name}' is disabled by server policy."
            safe_context = _safe_tool_event_context(getattr(request, "runtime", None))
            _audit_tool_safety(
                action="blocked",
                tool_name=tool_name,
                tool_call_id=str(tool_call_id),
                safe_context=safe_context,
                message=message,
            )
            try:
                await adispatch_custom_event(
                    "tool_blocked",
                    {
                        "event": "tool_blocked",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "message": message,
                        **safe_context,
                    },
                )
            except Exception as e:
                logger.debug("Failed to dispatch tool blocked event", error=str(e))

            return ToolMessage(
                content=message,
                tool_call_id=tool_call_id,
                name=tool_name,
                status="error",
            )

        return await handler(request)


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
