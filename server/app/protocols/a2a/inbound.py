"""Normalize inbound A2A message Parts without losing typed content."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from a2a.types import Part
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.agent.task_runtime import TaskInputArtifact


class InvalidA2APartError(ValueError):
    """Raised when an inbound A2A Part cannot be safely normalized."""


@dataclass(frozen=True)
class NormalizedA2AMessage:
    """Model-visible text plus scoped artifacts derived from A2A Parts."""

    content: str
    artifacts: tuple[TaskInputArtifact, ...]


def normalize_a2a_parts(
    parts: Iterable[Part],
    *,
    task_id: str,
    message_id: str | None,
    max_raw_part_bytes: int,
    max_parts: int = 64,
    max_message_bytes: int = 16 * 1024 * 1024,
    max_text_part_bytes: int = 2 * 1024 * 1024,
    max_data_part_bytes: int = 2 * 1024 * 1024,
) -> NormalizedA2AMessage:
    """Normalize all A2A 1.0 Part variants while preserving wire order.

    Raw bytes and URLs become inert artifact references. This function performs
    no persistence, execution, file parsing, or network access.
    """
    blocks: list[str] = []
    artifacts: list[TaskInputArtifact] = []
    stable_message_id = message_id or "anonymous"

    materialized = tuple(parts)
    if len(materialized) > max_parts:
        raise InvalidA2APartError(f"A2A message exceeds the {max_parts}-Part limit")
    total_bytes = 0

    for index, part in enumerate(materialized):
        kind = part.WhichOneof("content")
        if kind is None:
            raise InvalidA2APartError(f"A2A message Part {index} has no content")

        if kind == "text":
            size = len(part.text.encode("utf-8"))
            if size > max_text_part_bytes:
                raise InvalidA2APartError(
                    f"A2A text Part {index} exceeds the {max_text_part_bytes}-byte limit"
                )
            total_bytes += size
            blocks.append(part.text)
            continue
        if kind == "data":
            value = MessageToDict(part.data)
            compact_data = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            encoded_data = json.dumps(value, ensure_ascii=False, sort_keys=True)
            size = len(compact_data.encode("utf-8"))
            if size > max_data_part_bytes:
                raise InvalidA2APartError(
                    f"A2A data Part {index} exceeds the {max_data_part_bytes}-byte limit"
                )
            total_bytes += size
            blocks.append(
                f"[A2A data Part {index}]\n{encoded_data}"
            )
            continue

        artifact_id = _artifact_id(task_id, stable_message_id, index)
        media_type = part.media_type or None
        filename = part.filename or None
        metadata = MessageToDict(part.metadata) if part.HasField("metadata") else {}
        if kind == "raw":
            if len(part.raw) > max_raw_part_bytes:
                raise InvalidA2APartError(
                    f"A2A raw Part {index} exceeds the {max_raw_part_bytes}-byte limit"
                )
            content = base64.b64encode(part.raw).decode("ascii")
            content_encoding = "base64"
            total_bytes += len(part.raw)
        else:
            content = part.url
            content_encoding = "uri"
            total_bytes += len(part.url.encode("utf-8"))

        if total_bytes > max_message_bytes:
            raise InvalidA2APartError(
                f"A2A message exceeds the {max_message_bytes}-byte aggregate limit"
            )

        artifacts.append(
            TaskInputArtifact(
                id=artifact_id,
                kind=kind,
                content=content,
                content_encoding=content_encoding,
                media_type=media_type,
                filename=filename,
                metadata=metadata,
            )
        )
        details = json.dumps(
            {
                "artifact": f"/artifacts/{artifact_id}",
                "content_encoding": content_encoding,
                "filename": filename,
                "media_type": media_type,
                "part_kind": kind,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        blocks.append(f"[A2A {kind} Part {index}]\n{details}")

    if total_bytes > max_message_bytes:
        raise InvalidA2APartError(
            f"A2A message exceeds the {max_message_bytes}-byte aggregate limit"
        )

    return NormalizedA2AMessage(content="\n\n".join(blocks), artifacts=tuple(artifacts))


def _artifact_id(task_id: str, message_id: str, index: int) -> str:
    identity = f"cognition:a2a-input:{task_id}:{message_id}:{index}"
    return f"a2a-input-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


__all__ = [
    "InvalidA2APartError",
    "NormalizedA2AMessage",
    "normalize_a2a_parts",
]
