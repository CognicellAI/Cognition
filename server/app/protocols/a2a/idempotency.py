"""Canonical A2A request identity used for safe retry handling."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from a2a.types import SendMessageRequest
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]


def request_fingerprint(
    params: SendMessageRequest,
    *,
    agent_name: str,
    effective_scope: dict[str, str],
    task_id: str,
    context_id: str,
) -> str:
    """Hash the complete execution-relevant request using canonical ProtoJSON."""
    payload: dict[str, Any] = {
        "agent_name": agent_name,
        "effective_scope": effective_scope,
        "task_id": task_id,
        "context_id": context_id,
        "message": MessageToDict(params.message, preserving_proto_field_name=True),
        "configuration": (
            MessageToDict(params.configuration, preserving_proto_field_name=True)
            if params.HasField("configuration")
            else None
        ),
        "metadata": (
            MessageToDict(params.metadata, preserving_proto_field_name=True)
            if params.HasField("metadata")
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["request_fingerprint"]
