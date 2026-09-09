"""A2A transport binding for the optional A2UI extension."""

from __future__ import annotations

from typing import Any

from a2a.types import Part
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.a2ui.core import (
    _ASSET_DIGESTS,
    A2UI_EXTENSION_URI,
    A2UI_MEDIA_TYPE,
    BASIC_CATALOG_ID,
    MAX_A2UI_DEPTH,
    MAX_A2UI_MESSAGES,
    A2UIInvocationContext,
    A2UIValidationError,
    _base_media_type,
    _basic_catalog,
    _object_or_none,
    _string_list,
    _validate_depth,
    _validate_schema,
)
from server.app.a2ui.telemetry import record_negotiation
from server.app.agent.definition import A2AConfig


def negotiate_a2ui(
    *,
    config: A2AConfig,
    requested_extensions: tuple[str, ...],
    message_metadata: dict[str, object],
    message_parts: tuple[Part, ...],
    compatibility_alias_used: bool = False,
) -> A2UIInvocationContext | None:
    """Validate and resolve A2UI activation for one A2A request."""
    if config.a2ui is None:
        return None

    for key in ("a2uiRendererCapabilities", "a2uiRendererDataModel"):
        if key in message_metadata and not isinstance(message_metadata[key], dict):
            raise A2UIValidationError(f"{key} must be an object")
        _validate_depth(message_metadata.get(key), MAX_A2UI_DEPTH)
    explicit = A2UI_EXTENSION_URI in requested_extensions
    capabilities = _object_or_none(message_metadata.get("a2uiRendererCapabilities"))
    data_model = _object_or_none(message_metadata.get("a2uiRendererDataModel"))
    renderer_messages = _extract_renderer_messages(message_parts)
    if not explicit and capabilities is None:
        record_negotiation("ignored", "none")
        return None

    if capabilities is not None:
        version_caps = _object_or_none(capabilities.get("v1.0"))
        if version_caps is None:
            record_negotiation("rejected", "none")
            raise A2UIValidationError("A2UI renderer capabilities must include v1.0")
        inline = version_caps.get("inlineCatalogs")
        if inline:
            record_negotiation("rejected", "inline")
            raise A2UIValidationError("A2UI inline catalogs are not supported")
        _validate_schema("renderer_capabilities.json", capabilities, direction="input")
        renderer_catalog_ids = _string_list(version_caps.get("supportedCatalogIds"))
    else:
        renderer_catalog_ids = [BASIC_CATALOG_ID]

    configured_catalog_ids = tuple(config.a2ui.catalog_ids)
    selected = tuple(
        catalog for catalog in configured_catalog_ids if catalog in renderer_catalog_ids
    )
    if not selected:
        record_negotiation("rejected", "none")
        raise A2UIValidationError("No compatible A2UI catalog was negotiated")

    if data_model is not None:
        _validate_schema("renderer_data_model.json", data_model, direction="input")
    if renderer_messages:
        _validate_schema("renderer_to_agent_list.json", list(renderer_messages), direction="input")

    _validate_schema("catalog_definition.json", _basic_catalog(), direction="catalog")
    record_negotiation("activated", "basic")
    return A2UIInvocationContext(
        extension_uri=A2UI_EXTENSION_URI,
        catalog_ids=selected,
        catalog_digests=dict.fromkeys(selected, _ASSET_DIGESTS["catalogs/basic/catalog.json"]),
        renderer_capabilities=capabilities,
        renderer_data_model=data_model,
        renderer_messages=renderer_messages,
        explicit_activation=explicit,
        compatibility_alias_used=compatibility_alias_used,
    )


def _extract_renderer_messages(parts: tuple[Part, ...]) -> tuple[dict[str, Any], ...]:
    messages: list[dict[str, Any]] = []
    for part in parts:
        if part.WhichOneof("content") != "data":
            continue
        if _base_media_type(part.media_type) != A2UI_MEDIA_TYPE:
            continue
        value = MessageToDict(part.data)
        if not isinstance(value, list):
            raise A2UIValidationError("A2UI data Part value must be an array")
        for item in value:
            if not isinstance(item, dict):
                raise A2UIValidationError("A2UI data Part entries must be objects")
            messages.append(item)
    if len(messages) > MAX_A2UI_MESSAGES:
        raise A2UIValidationError("A2UI input exceeds the 64-message limit")
    _validate_depth(messages, MAX_A2UI_DEPTH)
    return tuple(messages)
