"""Pinned A2UI v1.0 assets, negotiation, and validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, Field, ValidationError, field_validator
from referencing import Registry, Resource

from server.app.a2ui.telemetry import record_validation

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

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Give model providers the concrete pinned message and catalog schema."""
        schema = super().model_json_schema(*args, **kwargs)
        schema["$defs"] = {
            "agent": _generation_schema(_schema("agent_to_renderer.json"), "agent"),
            "common": _generation_schema(_schema("common_types.json"), "common"),
            "catalog": _generation_schema(_basic_catalog(), "catalog"),
        }
        schema["properties"]["messages"].update(
            {
                "minItems": 1,
                "maxItems": MAX_A2UI_MESSAGES,
                "items": {"$ref": "#/$defs/agent"},
            }
        )
        return cast(dict[str, Any], _portable_generation_schema(schema, schema))

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


_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("a2ui-unicode-identifier")
def _unicode_identifier(value: object) -> bool:
    return not isinstance(value, str) or value.isidentifier()


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


def build_generation_prompt(context: A2UIInvocationContext) -> str:
    """Return request-scoped instructions and the pinned catalog for generation."""
    catalogs = ", ".join(context.catalog_ids)
    instructions = (
        "This request negotiated A2UI v1.0. Return the final answer using the "
        "internal structured response format with two fields: text and messages. "
        "The text field is optional conversational text for the user. The messages "
        "field must be a JSON array of complete A2UI v1.0 agent-to-renderer "
        f"messages using only these catalog IDs: {catalogs}. Start with "
        "createSurface when creating a new UI surface, then updateComponents or "
        "updateDataModel as needed. Each message object must contain version plus "
        "exactly one A2UI message key such as createSurface, updateComponents, "
        "or updateDataModel. Do not emit a type field, legacy A2A kind fields, "
        "or partial placeholder objects. A flat Text component is sufficient for simple "
        "surfaces. If you use Row or Column, their children must be component ID "
        "strings that refer to separate components in the same components array, "
        "not nested component objects. A minimal valid messages value is: "
        '[{"version":"v1.0","createSurface":{"surfaceId":"main",'
        '"components":[{"id":"root","component":"Text",'
        '"text":"Ready"}],"dataModel":{}}}]. To update an existing '
        "surface, preserve its existing component IDs and component types; update the "
        "relevant properties instead of replacing the layout. For an existing Text "
        'component with ID root only, use [{"version":"v1.0","updateComponents":'
        '{"surfaceId":"main","components":[{"id":"root",'
        '"component":"Text","text":"Updated"}]}}].'
    )

    return (
        instructions
        + "\nAuthoritative Basic catalog: "
        + json.dumps(_basic_catalog(), separators=(",", ":"))
    )


def normalize_a2ui_output(value: Any) -> A2UIResponseEnvelope:
    """Convert a Deep Agents structured response object into the internal envelope."""
    if isinstance(value, A2UIResponseEnvelope):
        return value
    if hasattr(value, "model_dump"):
        envelope: A2UIResponseEnvelope = _validate_envelope(value.model_dump(mode="json"))
        return envelope
    if isinstance(value, dict):
        envelope = _validate_envelope(value)
        return envelope
    raise A2UIValidationError(
        f"A2UI structured response has unsupported type {type(value).__name__}"
    )


def validate_agent_to_renderer_messages(
    messages: list[dict[str, Any]],
    catalog_ids: tuple[str, ...] = (BASIC_CATALOG_ID,),
) -> list[dict[str, Any]]:
    """Validate outbound A2UI messages against the pinned schema and catalog."""
    if not 1 <= len(messages) <= MAX_A2UI_MESSAGES:
        raise A2UIValidationError("A2UI output must contain 1 to 64 messages")
    _validate_depth(messages, MAX_A2UI_DEPTH)
    _validate_catalog_references(messages, catalog_ids)
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


def _validate_schema(name: str, value: Any, *, direction: str) -> None:
    validator = _validator(name)
    first = next(validator.iter_errors(value), None)
    if first is not None:
        record_validation(direction, "failure", _validation_reason(name))
        raise A2UIValidationError(f"A2UI {name} validation failed ({first.validator})")
    record_validation(direction, "success", "none")


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
    validator = Draft202012Validator(
        schema, registry=_schema_registry(), format_checker=_FORMAT_CHECKER
    )
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
    pairs = [(uri, Resource.from_contents(schema)) for uri, schema in _schema_store().items()]
    _REGISTRY = Registry().with_resources(pairs)
    return _REGISTRY


def _schema_store() -> dict[str, dict[str, Any]]:
    # Python re lacks the pinned schema's UAX #31 property escapes. Apply an
    # equivalent propertyNames format in the validation view only; asset bytes,
    # digests and generation schemas remain the original upstream files.
    common = json.loads(json.dumps(_schema("common_types.json")))
    common["$defs"]["Extensions"] = {
        "type": "object",
        "propertyNames": {"format": "a2ui-unicode-identifier"},
    }
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
        files("server.app.a2ui").joinpath("assets", "v1_0", *relative_path.split("/")).read_bytes()
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


def _validate_envelope(value: Any) -> A2UIResponseEnvelope:
    try:
        return A2UIResponseEnvelope.model_validate(value)
    except ValidationError as exc:
        raise A2UIValidationError("Invalid A2UI response envelope") from exc


def _validate_catalog_references(value: Any, catalog_ids: tuple[str, ...]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            # Application data is opaque; a field named catalogId inside a data
            # model or action context does not designate a renderer catalog.
            if key in {"dataModel", "context", "metadata"}:
                continue
            if key == "catalogId" and item not in catalog_ids:
                raise A2UIValidationError("A2UI output references an unnegotiated catalog")
            if key == "updateDataModel" and isinstance(item, dict):
                item = {name: child for name, child in item.items() if name != "value"}
            _validate_catalog_references(item, catalog_ids)
    elif isinstance(value, list):
        for item in value:
            _validate_catalog_references(item, catalog_ids)


def _generation_schema(value: Any, document: str) -> Any:
    """Rebase offline schema references into the provider envelope's definitions."""
    if isinstance(value, list):
        return [_generation_schema(item, document) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    documents = {
        "common_types.json": "common",
        "catalog.json": "catalog",
        "agent_to_renderer.json": "agent",
    }
    for key, item in value.items():
        if key in {"$id", "$schema", "description", "title"}:
            continue
        if key == "$ref":
            location, _, fragment = item.partition("#")
            target = documents[location.rsplit("/", 1)[-1]] if location else document
            result[key] = f"#/$defs/{target}{fragment}"
        else:
            result[key] = _generation_schema(item, document)
    return result


def _portable_generation_schema(
    value: Any, root: dict[str, Any], seen: frozenset[str] = frozenset()
) -> Any:
    """Lower pinned schema composition into a bounded model-facing superset.

    Providers differ in support for oneOf/allOf and recursive references. The
    generation schema exposes their concrete fields; exact semantics remain
    enforced by the unchanged authoritative validator before publication.
    """
    if isinstance(value, list):
        return [_portable_generation_schema(item, root, seen) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if ref in seen:
            return {}
        target: Any = root
        for segment in ref.removeprefix("#/").split("/"):
            target = target[segment.replace("~1", "/").replace("~0", "~")]
        return _portable_generation_schema(target, root, seen | {ref})
    result = {
        key: _portable_generation_schema(item, root, seen)
        for key, item in value.items()
        if key
        not in {
            "$defs",
            "oneOf",
            "anyOf",
            "allOf",
            "not",
            "unevaluatedProperties",
            "const",
            "patternProperties",
            "additionalProperties",
        }
    }
    if "const" in value:
        result["enum"] = [value["const"]]
        result.setdefault("type", "string")
    for keyword in ("allOf", "oneOf", "anyOf"):
        if keyword not in value:
            continue
        options = [_portable_generation_schema(option, root, seen) for option in value[keyword]]
        if all(
            option.get("type") == "object" or "properties" in option or set(option) <= {"required"}
            for option in options
        ):
            properties: dict[str, Any] = {}
            required_sets = [set(option.get("required", ())) for option in options]
            for option in options:
                for name, definition in option.get("properties", {}).items():
                    if name in properties and "enum" in definition and "enum" in properties[name]:
                        properties[name]["enum"] = list(
                            dict.fromkeys(properties[name]["enum"] + definition["enum"])
                        )
                    elif name in properties:
                        properties[name] = _generation_union(properties[name], definition)
                    else:
                        properties[name] = definition
            for name, definition in result.get("properties", {}).items():
                if definition or name not in properties:
                    properties[name] = definition
            result.update(type="object", properties=properties)
            required = (
                set.union(*required_sets)
                if keyword == "allOf"
                else set.intersection(*required_sets)
            )
            result["required"] = sorted(required | set(result.get("required", ())))
        elif len(options) == 1:
            result.update(options[0])
        else:
            result["anyOf"] = options
    return result


def _generation_union(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if not left or not right:
        return {}
    alternatives = []
    for schema in (left, right):
        for item in schema.get("anyOf", [schema]):
            if item not in alternatives:
                alternatives.append(item)
    return alternatives[0] if len(alternatives) == 1 else {"anyOf": alternatives}
