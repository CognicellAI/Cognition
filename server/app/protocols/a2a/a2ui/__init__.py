"""A2UI v1.0 support for Cognition's A2A adapter."""

from server.app.protocols.a2a.a2ui.core import (
    A2UI_EXTENSION_URI,
    A2UI_MEDIA_TYPE,
    BASIC_CATALOG_ID,
    A2UIInvocationContext,
    A2UIResponseEnvelope,
    A2UIValidationError,
    build_agent_card_extension_params,
    build_generation_prompt,
    build_unknown_agent_function_responses,
    has_agent_function_calls,
    negotiate_a2ui,
    normalize_a2ui_output,
    validate_agent_to_renderer_messages,
)

__all__ = [
    "A2UI_EXTENSION_URI",
    "A2UI_MEDIA_TYPE",
    "BASIC_CATALOG_ID",
    "A2UIInvocationContext",
    "A2UIResponseEnvelope",
    "A2UIValidationError",
    "build_agent_card_extension_params",
    "build_generation_prompt",
    "build_unknown_agent_function_responses",
    "has_agent_function_calls",
    "normalize_a2ui_output",
    "negotiate_a2ui",
    "validate_agent_to_renderer_messages",
]
