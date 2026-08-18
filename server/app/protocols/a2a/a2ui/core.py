"""Pinned A2UI v1.0 assets, negotiation, and validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, cast

from a2a.types import Part
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, field_validator
from referencing import Registry, Resource

from server.app.agent.definition import A2AConfig
from server.app.observability import A2UI_NEGOTIATIONS_TOTAL, A2UI_VALIDATIONS_TOTAL

A2UI_EXTENSION_URI = "https://a2ui.org/a2a-extension/a2ui/v1.0"
A2UI_MEDIA_TYPE = "application/a2ui+json"
A2UI_VERSION = "v1.0"
BASIC_CATALOG_ID = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
A2UI_UPSTREAM_REVISION = "44a420b67957fafc0b02d55a153fdaf72e32ffb5"

MAX_A2UI_MESSAGES = 64
MAX_A2UI_DEPTH = 32

_ASSET_DIGESTS = {
    "json/common_types.json": "169ff987a7f8fb93a040ca33352ae4aeb684ff9742bff8cf5925b5cd2612e7d8",
    "json/agent_capabilities.json": "86318a04b2fda504bededba936880e1d9cd5bcbb856b6aac6377f60e0426fbe9",
    "json/agent_to_renderer_list.json": "3d3fef882fe5cc22932d3a91713defaeeb329126200954c06bf9f2c56df51c56",
    "json/renderer_to_agent_list.json": "98899d7d16222207af3bfd9aab12aa7b8f68dc8b5267dcf76d867236235ec5d8",
    "json/renderer_capabilities.json": "923293ac283424ca08b68a951bc110f8885350baf4c8a9cb678c1647f0b22b3b",
    "json/renderer_data_model.json": "be0890fce16c10f903bd30808e0ec012c7766212fb95c983319db73faac485eb",
    "json/catalog_definition.json": "ac02e04ff1deb3a1832b9b111c8509e6c8ed7976f630025b822763e975172d46",
    "json/agent_to_renderer.json": "468c8e544dbe0b02d5d5586ebcdca399ecfa6d07a7b875d887089ce3bd2df160",
    "json/renderer_to_agent.json": "b23e1ca3e311f20d8a3b48620f4ca2e13a4188acd55de973075db421a0ed7e69",
    "catalogs/basic/catalog.json": "29e01ac2cf69dc5860ad060f5a60c67fa5cdaa8a78ecab1018f531b178fa5c00",
}


class A2UIValidationError(ValueError):
    """Raised when A2UI negotiation or payload validation fails."""


class A2UIResponseEnvelope(BaseModel):
    """Internal structured-output envelope for an active A2UI request."""

    text: str | None = Field(default=None, max_length=16000)
    messages: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not value:
            raise ValueError("A2UI output must contain at least one message")
        if len(value) > MAX_A2UI_MESSAGES:
            raise ValueError(f"A2UI output exceeds the {MAX_A2UI_MESSAGES}-message limit")
        _validate_depth(value, MAX_A2UI_DEPTH)
        return value


@dataclass(frozen=True)
class A2UIInvocationContext:
    """Request-scoped A2UI negotiation result."""

    extension_uri: str
    catalog_ids: tuple[str, ...]
    catalog_digests: dict[str, str]
    renderer_capabilities: dict[str, Any] | None = None
    renderer_data_model: dict[str, Any] | None = None
    renderer_messages: tuple[dict[str, Any], ...] = ()
    explicit_activation: bool = False
    compatibility_alias_used: bool = False

    def to_metadata(self) -> dict[str, Any]:
        """Return safe metadata for a run without renderer state or scope values."""
        return {
            "extension_uri": self.extension_uri,
            "catalog_ids": list(self.catalog_ids),
            "catalog_digests": dict(self.catalog_digests),
            "explicit_activation": self.explicit_activation,
            "compatibility_alias_used": self.compatibility_alias_used,
        }


_SCHEMAS: dict[str, dict[str, Any]] = {}
_VALIDATORS: dict[str, Draft202012Validator] = {}
_BASIC_CATALOG: dict[str, Any] | None = None
_REGISTRY: Registry[dict[str, Any]] | None = None


def build_agent_card_extension_params() -> dict[str, Any]:
    """Return the A2UI Agent Card extension params for the pinned Basic catalog."""
    return {
        "supportedCatalogIds": [BASIC_CATALOG_ID],
        "acceptsInlineCatalogs": False,
    }


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

    explicit = A2UI_EXTENSION_URI in requested_extensions
    capabilities = _object_or_none(message_metadata.get("a2uiRendererCapabilities"))
    data_model = _object_or_none(message_metadata.get("a2uiRendererDataModel"))
    renderer_messages = _extract_renderer_messages(message_parts)
    if not explicit and capabilities is None:
        A2UI_NEGOTIATIONS_TOTAL.labels(outcome="ignored", catalog="none").inc()
        return None

    if capabilities is not None:
        _validate_schema("renderer_capabilities.json", capabilities, direction="input")
        version_caps = _object_or_none(capabilities.get("v1.0"))
        if version_caps is None:
            A2UI_NEGOTIATIONS_TOTAL.labels(outcome="rejected", catalog="none").inc()
            raise A2UIValidationError("A2UI renderer capabilities must include v1.0")
        inline = version_caps.get("inlineCatalogs")
        if inline:
            A2UI_NEGOTIATIONS_TOTAL.labels(outcome="rejected", catalog="inline").inc()
            raise A2UIValidationError("A2UI inline catalogs are not supported")
        renderer_catalog_ids = _string_list(version_caps.get("supportedCatalogIds"))
    else:
        renderer_catalog_ids = [BASIC_CATALOG_ID]

    configured_catalog_ids = tuple(config.a2ui.catalog_ids)
    selected = tuple(catalog for catalog in configured_catalog_ids if catalog in renderer_catalog_ids)
    if not selected:
        A2UI_NEGOTIATIONS_TOTAL.labels(outcome="rejected", catalog="none").inc()
        raise A2UIValidationError("No compatible A2UI catalog was negotiated")

    if data_model is not None:
        _validate_schema("renderer_data_model.json", data_model, direction="input")
    if renderer_messages:
        _validate_schema("renderer_to_agent_list.json", list(renderer_messages), direction="input")

    _validate_schema("catalog_definition.json", _basic_catalog(), direction="catalog")
    A2UI_NEGOTIATIONS_TOTAL.labels(outcome="activated", catalog="basic").inc()
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


def build_generation_prompt(context: A2UIInvocationContext) -> str:
    """Return concise request-scoped instructions for A2UI structured output."""
    catalogs = ", ".join(context.catalog_ids)
    return (
        "This request negotiated A2UI v1.0. Return the final answer using the "
        "internal structured response format with two fields: text and messages. "
        "The text field is optional conversational text for the user. The messages "
        "field must be a JSON array of complete A2UI v1.0 agent-to-renderer "
        f"messages using only these catalog IDs: {catalogs}. Start with "
        "createSurface when creating a new UI surface, then updateComponents or "
        "updateDataModel as needed. Each message object must contain version plus "
        "exactly one A2UI message key such as createSurface, updateComponents, "
        "or updateDataModel. Do not emit a type field, legacy A2A kind fields, "
        "or partial placeholder objects. Prefer a flat Text component for compact "
        "surfaces. If you use Row or Column, their children must be component ID "
        "strings that refer to separate components in the same components array, "
        "not nested component objects. A minimal valid messages value is: "
        "[{\"version\":\"v1.0\",\"createSurface\":{\"surfaceId\":\"main\","
        "\"components\":[{\"id\":\"root\",\"component\":\"Text\","
        "\"text\":\"Ready\"}],\"dataModel\":{}}}]. To update an existing "
        "surface, use [{\"version\":\"v1.0\",\"updateComponents\":"
        "{\"surfaceId\":\"main\",\"components\":[{\"id\":\"root\","
        "\"component\":\"Text\",\"text\":\"Updated\"}]}}]."
    )


def normalize_a2ui_output(value: Any) -> A2UIResponseEnvelope:
    """Convert a Deep Agents structured response object into the internal envelope."""
    if isinstance(value, A2UIResponseEnvelope):
        return value
    if hasattr(value, "model_dump"):
        envelope: A2UIResponseEnvelope = A2UIResponseEnvelope.model_validate(
            value.model_dump(mode="json")
        )
        return envelope
    if isinstance(value, dict):
        envelope = A2UIResponseEnvelope.model_validate(value)
        return envelope
    raise A2UIValidationError(
        f"A2UI structured response has unsupported type {type(value).__name__}"
    )


def validate_agent_to_renderer_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate outbound A2UI messages against the pinned schema and catalog."""
    _validate_depth(messages, MAX_A2UI_DEPTH)
    _validate_schema("agent_to_renderer_list.json", messages, direction="output")
    for message in messages:
        if not isinstance(message, dict) or message.get("version") != A2UI_VERSION:
            raise A2UIValidationError("A2UI messages must declare version v1.0")
    return messages


def has_agent_function_calls(context: A2UIInvocationContext) -> bool:
    """Return whether renderer input includes Agent function calls."""
    return any("callAgentFunction" in message for message in context.renderer_messages)


def build_unknown_agent_function_responses(
    context: A2UIInvocationContext,
) -> list[dict[str, Any]]:
    """Build explicit A2UI errors for unsupported renderer Agent function calls."""
    responses: list[dict[str, Any]] = []
    for message in context.renderer_messages:
        call_agent_function = _object_or_none(message.get("callAgentFunction"))
        if call_agent_function is None:
            continue
        function_call_id = str(call_agent_function.get("functionCallId") or "")
        call_function = _object_or_none(call_agent_function.get("callFunction"))
        function_name = (
            str(call_function.get("call"))
            if call_function is not None and call_function.get("call") is not None
            else "unknown"
        )
        responses.append(
            {
                "version": A2UI_VERSION,
                "agentFunctionResponse": {
                    "functionCallId": function_call_id,
                    "error": {
                        "code": "UNKNOWN_AGENT_FUNCTION",
                        "message": (
                            "Cognition does not expose an Agent function registry; "
                            f"function {function_name!r} is not available."
                        ),
                    },
                },
            }
        )
    return validate_agent_to_renderer_messages(responses)


def pinned_asset_manifest() -> dict[str, Any]:
    """Return the pinned A2UI asset manifest for runtime manifests and docs."""
    return {
        "extension_uri": A2UI_EXTENSION_URI,
        "version": "1.0",
        "status": "candidate",
        "upstream_revision": A2UI_UPSTREAM_REVISION,
        "catalogs": {
            "basic": {
                "id": BASIC_CATALOG_ID,
                "digest": _ASSET_DIGESTS["catalogs/basic/catalog.json"],
            }
        },
        "asset_digests": dict(_ASSET_DIGESTS),
    }


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
    return tuple(messages)


def _validate_schema(name: str, value: Any, *, direction: str) -> None:
    validator = _validator(name)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path) or "$"
        A2UI_VALIDATIONS_TOTAL.labels(
            direction=direction,
            outcome="failure",
            reason=_validation_reason(name),
        ).inc()
        raise A2UIValidationError(f"A2UI {name} validation failed at {path}: {first.message}")
    A2UI_VALIDATIONS_TOTAL.labels(
        direction=direction,
        outcome="success",
        reason="none",
    ).inc()


def _validation_reason(name: str) -> str:
    if name.startswith("renderer_"):
        return "renderer_schema"
    if name.startswith("agent_"):
        return "agent_schema"
    if name.startswith("catalog"):
        return "catalog_schema"
    return "schema"


def _validator(name: str) -> Draft202012Validator:
    existing = _VALIDATORS.get(name)
    if existing is not None:
        return existing
    schema = _schema(name)
    validator = Draft202012Validator(schema, registry=_schema_registry())
    Draft202012Validator.check_schema(schema)
    _VALIDATORS[name] = validator
    return validator


def _schema(name: str) -> dict[str, Any]:
    existing = _SCHEMAS.get(name)
    if existing is not None:
        return existing
    data = _load_json(f"json/{name}")
    _SCHEMAS[name] = data
    return data


def _basic_catalog() -> dict[str, Any]:
    global _BASIC_CATALOG
    if _BASIC_CATALOG is None:
        _BASIC_CATALOG = _load_json("catalogs/basic/catalog.json")
    return _BASIC_CATALOG


def _schema_registry() -> Registry[dict[str, Any]]:
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    pairs = [
        (uri, Resource.from_contents(schema))
        for uri, schema in _schema_store().items()
    ]
    _REGISTRY = Registry().with_resources(pairs)
    return _REGISTRY


def _schema_store() -> dict[str, dict[str, Any]]:
    common = _schema("common_types.json")
    catalog = _basic_catalog()
    catalog_definition = _schema("catalog_definition.json")
    agent_to_renderer = _schema("agent_to_renderer.json")
    renderer_to_agent = _schema("renderer_to_agent.json")
    return {
        "https://a2ui.org/specification/v1_0/common_types.json": common,
        "https://a2ui.org/specification/v1_0/json/common_types.json": common,
        "common_types.json": common,
        "https://a2ui.org/specification/v1_0/catalog_definition.json": catalog_definition,
        "https://a2ui.org/specification/v1_0/json/catalog_definition.json": catalog_definition,
        "catalog_definition.json": catalog_definition,
        "https://a2ui.org/specification/v1_0/agent_to_renderer.json": agent_to_renderer,
        "https://a2ui.org/specification/v1_0/json/agent_to_renderer.json": agent_to_renderer,
        "agent_to_renderer.json": agent_to_renderer,
        "https://a2ui.org/specification/v1_0/renderer_to_agent.json": renderer_to_agent,
        "https://a2ui.org/specification/v1_0/json/renderer_to_agent.json": renderer_to_agent,
        "renderer_to_agent.json": renderer_to_agent,
        "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json": catalog,
        "https://a2ui.org/specification/v1_0/catalog.json": catalog,
        "https://a2ui.org/specification/v1_0/json/catalog.json": catalog,
        "catalog.json": catalog,
    }


def _load_json(relative_path: str) -> dict[str, Any]:
    data = (
        files("server.app.protocols.a2a.a2ui")
        .joinpath("assets", "v1_0", *relative_path.split("/"))
        .read_bytes()
    )
    expected = _ASSET_DIGESTS[relative_path]
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise A2UIValidationError(
            f"Bundled A2UI asset {relative_path} digest {actual} does not match {expected}"
        )
    loaded = json.loads(data)
    if not isinstance(loaded, dict):
        raise A2UIValidationError(f"Bundled A2UI asset {relative_path} must be a JSON object")
    return cast(dict[str, Any], loaded)


def _object_or_none(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise A2UIValidationError("A2UI catalog IDs must be a string array")
    return list(value)


def _base_media_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def _validate_depth(value: object, max_depth: int, depth: int = 0) -> None:
    if depth > max_depth:
        raise A2UIValidationError(f"A2UI payload exceeds depth limit {max_depth}")
    if isinstance(value, dict):
        for item in value.values():
            _validate_depth(item, max_depth, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_depth(item, max_depth, depth + 1)
