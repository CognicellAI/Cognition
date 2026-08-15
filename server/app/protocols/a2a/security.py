"""Deployment-level A2A Agent Card security discovery configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from a2a.types import SecurityRequirement, SecurityScheme
from a2a.utils.proto_utils import validate_proto_required_fields
from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]


class A2ASecurityConfigurationError(ValueError):
    """Raised when public A2A security discovery metadata is invalid."""


@dataclass(frozen=True)
class A2ACardSecurity:
    """Validated security metadata shared by generated Agent Cards."""

    schemes: dict[str, SecurityScheme]
    requirements: tuple[SecurityRequirement, ...]


def parse_a2a_card_security(
    schemes_data: dict[str, dict[str, Any]],
    requirements_data: list[dict[str, Any]],
) -> A2ACardSecurity:
    """Parse and cross-validate canonical A2A ProtoJSON security metadata.

    Args:
        schemes_data: Map of public scheme names to ``SecurityScheme`` ProtoJSON.
        requirements_data: List of ``SecurityRequirement`` ProtoJSON objects.

    Returns:
        Typed protobuf security metadata suitable for an ``AgentCard``.

    Raises:
        A2ASecurityConfigurationError: If the ProtoJSON is invalid or a requirement
            references an undeclared scheme.
    """
    schemes: dict[str, SecurityScheme] = {}
    for name, payload in schemes_data.items():
        if not name:
            raise A2ASecurityConfigurationError(
                "COGNITION_A2A_SECURITY_SCHEMES contains an empty scheme name"
            )
        try:
            scheme = ParseDict(
                payload,
                SecurityScheme(),
                ignore_unknown_fields=False,
            )
            if scheme.WhichOneof("scheme") is None:
                raise ValueError("exactly one security scheme variant is required")
            validate_proto_required_fields(scheme)
        except Exception as exc:
            raise A2ASecurityConfigurationError(
                f"COGNITION_A2A_SECURITY_SCHEMES[{name!r}] is invalid: {exc}"
            ) from exc
        schemes[name] = scheme

    requirements: list[SecurityRequirement] = []
    for index, payload in enumerate(requirements_data):
        try:
            requirement = ParseDict(
                payload,
                SecurityRequirement(),
                ignore_unknown_fields=False,
            )
            validate_proto_required_fields(requirement)
        except Exception as exc:
            raise A2ASecurityConfigurationError(
                f"COGNITION_A2A_SECURITY_REQUIREMENTS[{index}] is invalid: {exc}"
            ) from exc

        undeclared = sorted(set(requirement.schemes) - set(schemes))
        if undeclared:
            names = ", ".join(undeclared)
            raise A2ASecurityConfigurationError(
                "COGNITION_A2A_SECURITY_REQUIREMENTS"
                f"[{index}] references undeclared schemes: {names}"
            )
        requirements.append(requirement)

    return A2ACardSecurity(schemes=schemes, requirements=tuple(requirements))


__all__ = [
    "A2ACardSecurity",
    "A2ASecurityConfigurationError",
    "parse_a2a_card_security",
]
