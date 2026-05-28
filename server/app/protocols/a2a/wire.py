"""A2A JSON-RPC wire-shape helpers.

The installed Python SDK still exposes a protobuf-oriented JSON-RPC dispatcher
whose method names and enum values differ from the public A2A JSON binding.
Cognition keeps those details private and exposes the current A2A shape at the
HTTP boundary.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CURRENT_A2A_VERSION = "1.0"

_PUBLIC_TO_SDK_METHOD = {
    "message/send": "SendMessage",
    "message/stream": "SendStreamingMessage",
}

_STATE_TO_CURRENT = {
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_WORKING": "working",
    "TASK_STATE_INPUT_REQUIRED": "input-required",
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_CANCELED": "canceled",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_REJECTED": "rejected",
    "TASK_STATE_AUTH_REQUIRED": "auth-required",
    1: "submitted",
    2: "working",
    3: "completed",
    4: "canceled",
    5: "failed",
    6: "rejected",
    7: "input-required",
    8: "auth-required",
}

_ROLE_TO_SDK = {
    "user": "ROLE_USER",
    "agent": "ROLE_AGENT",
    "assistant": "ROLE_AGENT",
}

_ROLE_TO_CURRENT = {
    "ROLE_USER": "user",
    "ROLE_AGENT": "agent",
}


def is_public_a2a_method(method: str | None) -> bool:
    """Return true when the JSON-RPC method is part of Cognition's public A2A API."""
    return method in _PUBLIC_TO_SDK_METHOD


def normalize_request_for_sdk(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a public A2A JSON-RPC request into the SDK's internal shape."""
    normalized = deepcopy(payload)
    method = normalized.get("method")
    if method in _PUBLIC_TO_SDK_METHOD:
        normalized["method"] = _PUBLIC_TO_SDK_METHOD[method]

    params = normalized.get("params")
    if not isinstance(params, dict):
        return normalized

    message = params.get("message")
    if not isinstance(message, dict):
        return normalized

    role = message.get("role")
    if isinstance(role, str):
        message["role"] = _ROLE_TO_SDK.get(role.lower(), role)

    parts = message.get("parts")
    if isinstance(parts, list):
        message["parts"] = [_normalize_part_for_sdk(part) for part in parts]

    return normalized


def normalize_response_to_public(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate SDK JSON-RPC response data into the current public A2A shape."""
    normalized = deepcopy(payload)
    result = normalized.get("result")
    if isinstance(result, dict):
        normalized["result"] = _normalize_result_to_public(result)
    return normalized


def normalize_stream_item_to_public(item: dict[str, Any]) -> dict[str, Any]:
    """Translate one SDK SSE JSON-RPC item into the current public A2A shape."""
    return normalize_response_to_public(item)


def _normalize_part_for_sdk(part: Any) -> Any:
    if not isinstance(part, dict):
        return part

    if part.get("kind") == "text" and "text" in part:
        normalized = {k: v for k, v in part.items() if k != "kind"}
        normalized.setdefault("mediaType", "text/plain")
        return normalized

    return part


def _normalize_result_to_public(result: dict[str, Any]) -> dict[str, Any]:
    if "task" in result and isinstance(result["task"], dict):
        return _normalize_task_to_public(result["task"])
    if "message" in result and isinstance(result["message"], dict):
        return _normalize_message_to_public(result["message"])
    if _looks_like_status_update(result):
        return _normalize_status_update_to_public(result)
    if _looks_like_artifact_update(result):
        return _normalize_artifact_update_to_public(result)
    if _looks_like_task(result):
        return _normalize_task_to_public(result)
    return result


def _normalize_task_to_public(task: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(task)
    normalized["kind"] = "task"
    status = normalized.get("status")
    if isinstance(status, dict):
        normalized["status"] = _normalize_status_to_public(status)
    artifacts = normalized.get("artifacts")
    if isinstance(artifacts, list):
        normalized["artifacts"] = [
            _normalize_artifact_to_public(artifact) for artifact in artifacts
        ]
    history = normalized.get("history")
    if isinstance(history, list):
        normalized["history"] = [
            _normalize_message_to_public(message) for message in history
        ]
    return normalized


def _normalize_message_to_public(message: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(message)
    normalized["kind"] = "message"
    role = normalized.get("role")
    if isinstance(role, str):
        normalized["role"] = _ROLE_TO_CURRENT.get(role, role.lower())
    parts = normalized.get("parts")
    if isinstance(parts, list):
        normalized["parts"] = [_normalize_part_to_public(part) for part in parts]
    return normalized


def _normalize_status_update_to_public(update: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(update)
    normalized["kind"] = "status-update"
    status = normalized.get("status")
    if isinstance(status, dict):
        normalized["status"] = _normalize_status_to_public(status)
    return normalized


def _normalize_artifact_update_to_public(update: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(update)
    normalized["kind"] = "artifact-update"
    artifact = normalized.get("artifact")
    if isinstance(artifact, dict):
        normalized["artifact"] = _normalize_artifact_to_public(artifact)
    return normalized


def _normalize_status_to_public(status: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(status)
    state = normalized.get("state")
    normalized["state"] = _normalize_state_to_public(state)
    message = normalized.get("message")
    if isinstance(message, dict):
        normalized["message"] = _normalize_message_to_public(message)
    return normalized


def _normalize_artifact_to_public(artifact: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(artifact)
    normalized.setdefault("kind", "artifact")
    parts = normalized.get("parts")
    if isinstance(parts, list):
        normalized["parts"] = [_normalize_part_to_public(part) for part in parts]
    return normalized


def _normalize_part_to_public(part: Any) -> Any:
    if not isinstance(part, dict):
        return part
    normalized = deepcopy(part)
    if "text" in normalized:
        normalized.setdefault("kind", "text")
    return normalized


def _normalize_state_to_public(state: Any) -> Any:
    if isinstance(state, str):
        mapped = _STATE_TO_CURRENT.get(state)
        if mapped:
            return mapped
        return state.lower().replace("task_state_", "").replace("_", "-")
    return _STATE_TO_CURRENT.get(state, state)


def _looks_like_task(value: dict[str, Any]) -> bool:
    return "id" in value and "contextId" in value and "status" in value


def _looks_like_status_update(value: dict[str, Any]) -> bool:
    return "taskId" in value and "status" in value


def _looks_like_artifact_update(value: dict[str, Any]) -> bool:
    return "taskId" in value and "artifact" in value
