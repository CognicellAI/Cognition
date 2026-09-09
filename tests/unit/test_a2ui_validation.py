"""A2UI candidate schema and negotiation boundary regressions."""

from __future__ import annotations

import pytest

from server.app.agent.definition import A2AConfig
from server.app.protocols.a2a.a2ui import (
    A2UI_EXTENSION_URI,
    A2UIValidationError,
    negotiate_a2ui,
    validate_agent_to_renderer_messages,
)


def test_unnegotiated_surface_catalog_is_rejected():
    with pytest.raises(A2UIValidationError):
        validate_agent_to_renderer_messages(
            [
                {
                    "version": "v1.0",
                    "createSurface": {
                        "surfaceId": "main",
                        "catalogId": "https://foreign.example/catalog",
                    },
                }
            ]
        )


def test_valid_unicode_extension_names_are_supported():
    payload = [
        {
            "version": "v1.0",
            "createSurface": {
                "surfaceId": "main",
                "metadata": {"extensions": {"vendor_x": True, "étiquette": True}},
            },
        }
    ]
    assert validate_agent_to_renderer_messages(payload) == payload


@pytest.mark.parametrize(
    "key,value",
    [
        ("a2uiRendererCapabilities", "invalid"),
        ("a2uiRendererDataModel", ["invalid"]),
        ("a2uiRendererCapabilities", None),
    ],
)
def test_supplied_invalid_metadata_is_rejected(key, value):
    config = A2AConfig.model_validate(
        {"exposed": True, "a2ui": {"version": "1.0", "catalogs": ["basic"]}}
    )
    with pytest.raises(A2UIValidationError):
        negotiate_a2ui(
            config=config,
            requested_extensions=(A2UI_EXTENSION_URI,),
            message_metadata={key: value},
            message_parts=(),
        )


@pytest.mark.parametrize(
    "component",
    [
        {"component": "CheckBox", "id": "root", "label": "Ready", "value": True},
        {"component": "Slider", "id": "root", "value": 5, "min": 0, "max": 10},
        {"component": "Text", "id": "root", "text": "Ready"},
    ],
)
def test_generation_schema_accepts_valid_basic_control_types(component):
    import json

    from jsonschema import Draft202012Validator

    from server.app.a2ui.core import BASIC_CATALOG_ID, A2UIResponseEnvelope

    messages = [
        {
            "version": "v1.0",
            "createSurface": {
                "surfaceId": "main",
                "catalogId": BASIC_CATALOG_ID,
                "components": [component],
            },
        }
    ]
    validate_agent_to_renderer_messages(messages)
    schema = A2UIResponseEnvelope.model_json_schema()
    Draft202012Validator(schema).validate({"text": "Ready", "messages": messages})
    assert len(json.dumps(schema)) < 64 * 1024


def test_invalid_extension_identifier_is_rejected():
    with pytest.raises(A2UIValidationError):
        validate_agent_to_renderer_messages(
            [
                {
                    "version": "v1.0",
                    "createSurface": {
                        "surfaceId": "main",
                        "metadata": {"extensions": {"not valid": True}},
                    },
                }
            ]
        )


def test_opaque_data_model_catalog_field_is_not_a_catalog_reference():
    messages = [
        {
            "version": "v1.0",
            "updateDataModel": {"surfaceId": "main", "value": {"catalogId": "customer inventory"}},
        }
    ]
    assert validate_agent_to_renderer_messages(messages) == messages


@pytest.mark.parametrize(
    "payload",
    [
        [{"version": "v1.0", "createSurface": {"surfaceId": "main"}}] * 65,
        [{"version": "v1.0", "updateDataModel": {"surfaceId": "main", "value": None}}],
    ],
)
def test_output_bounds_precede_schema_validation(payload, monkeypatch):
    from server.app.a2ui import core

    if len(payload) == 1:
        nested = {}
        cursor = nested
        for _ in range(40):
            cursor["nested"] = {}
            cursor = cursor["nested"]
        payload[0]["updateDataModel"]["value"] = nested

    def unexpected_schema_validation(*args, **kwargs):
        pytest.fail("Bounds must reject before schema traversal")

    monkeypatch.setattr(core, "_validate_schema", unexpected_schema_validation)
    with pytest.raises(A2UIValidationError):
        validate_agent_to_renderer_messages(payload)


def test_inline_catalog_is_rejected_before_schema_validation(monkeypatch):
    from server.app.protocols.a2a.a2ui import core

    def unexpected_schema_validation(*args, **kwargs):
        pytest.fail("Inline catalog admission must precede schema traversal")

    monkeypatch.setattr(core, "_validate_schema", unexpected_schema_validation)
    config = A2AConfig.model_validate({"exposed": True, "a2ui": {"version": "1.0"}})
    with pytest.raises(A2UIValidationError, match="inline catalogs"):
        negotiate_a2ui(
            config=config,
            requested_extensions=(A2UI_EXTENSION_URI,),
            message_metadata={
                "a2uiRendererCapabilities": {
                    "v1.0": {"supportedCatalogIds": [], "inlineCatalogs": [{"malformed": True}]}
                }
            },
            message_parts=(),
        )


def test_unnegotiated_component_catalog_is_rejected():
    with pytest.raises(A2UIValidationError, match="unnegotiated"):
        validate_agent_to_renderer_messages(
            [
                {
                    "version": "v1.0",
                    "createSurface": {
                        "surfaceId": "main",
                        "components": [
                            {
                                "component": "Text",
                                "id": "root",
                                "text": "Ready",
                                "catalogId": "https://foreign.example/catalog",
                            }
                        ],
                    },
                }
            ]
        )
