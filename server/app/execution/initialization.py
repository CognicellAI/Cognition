"""Bounded transport to a deployment-owned runtime initialization authority."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import SecretStr


def bind_runtime_initializer(
    *,
    url: str,
    token: SecretStr,
    scope: dict[str, str],
    profile_name: str,
    image_arn: str,
    image_version: str | None,
    maximum_duration_seconds: int,
    ca_file: str | None = None,
) -> Callable[[str], dict[str, Any]]:
    """Bind trusted construction context, never model-supplied callback arguments.

    The external authority validates the binding and returns an opaque JSON
    object. Authorization and credential issuance remain outside Cognition.
    """
    context = {
        "effective_scope": dict(scope),
        "profile_name": profile_name,
        "image_arn": image_arn,
        "image_version": image_version,
        "maximum_duration_seconds": maximum_duration_seconds,
    }

    def initialize(microvm_id: str) -> dict[str, Any]:
        try:
            request = json.dumps({**context, "microvm_id": microvm_id}, allow_nan=False)
            if len(request.encode()) > 16384:
                raise ValueError("Initialization context exceeds limit")
            with httpx.Client(
                timeout=10,
                follow_redirects=False,
                trust_env=False,
                verify=ca_file or True,
            ) as client:
                with client.stream(
                    "POST", url, content=request,
                    headers={"Authorization": "Bearer " + token.get_secret_value(),
                             "Content-Type": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        raise ValueError("Initialization authority rejected request")
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if len(body) + len(chunk) > 16384:
                            raise ValueError("Initialization response exceeds limit")
                        body.extend(chunk)
                    payload = json.loads(body)
                    if not isinstance(payload, dict):
                        raise ValueError("Initialization authority returned a non-object")
                    return payload
        except Exception:
            raise RuntimeError("Sandbox initialization authority request failed") from None

    return initialize
