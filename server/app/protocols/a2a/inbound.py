"""Normalize inbound A2A message Parts without losing typed content."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from a2a.types import Part
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]

from server.app.agent.task_runtime import TaskInputPart


class InvalidA2APartError(ValueError):
    """Raised when an inbound A2A Part cannot be safely normalized."""


@dataclass(frozen=True)
class NormalizedA2AMessage:
    """Model-visible rendering plus lossless canonical A2A Parts."""

    content: str
    parts: tuple[TaskInputPart, ...]
    message_id: str
    metadata: dict[str, object]
    extensions: tuple[str, ...]
    reference_task_ids: tuple[str, ...]

    @property
    def artifacts(self) -> tuple[TaskInputPart, ...]:
        """Return file-like Parts addressable through the artifact API."""
        return tuple(part for part in self.parts if part.kind in {"raw", "url"})


@dataclass(frozen=True)
class _CanonicalMessageContext:
    metadata: dict[str, object]
    extensions: tuple[str, ...]
    reference_task_ids: tuple[str, ...]
    model_block: str | None
    byte_size: int


@dataclass(frozen=True)
class _NormalizedPart:
    part: TaskInputPart
    model_block: str
    byte_size: int


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
    message_metadata: dict[str, object] | None = None,
    message_extensions: Sequence[str] = (),
    reference_task_ids: Sequence[str] = (),
) -> NormalizedA2AMessage:
    """Normalize all A2A 1.0 Part variants while preserving wire order.

    Raw bytes and URLs become inert artifact references. This function performs
    no persistence, execution, file parsing, or network access.
    """
    materialized = tuple(parts)
    if len(materialized) > max_parts:
        raise InvalidA2APartError(f"A2A message exceeds the {max_parts}-Part limit")

    stable_message_id = message_id or "anonymous"
    context = _canonicalize_message_context(
        metadata=message_metadata,
        extensions=message_extensions,
        reference_task_ids=reference_task_ids,
    )
    blocks = [context.model_block] if context.model_block is not None else []
    normalized_parts: list[TaskInputPart] = []
    total_bytes = context.byte_size
    _enforce_aggregate_limit(total_bytes, max_message_bytes)

    for index, part in enumerate(materialized):
        normalized = _normalize_part(
            part,
            index=index,
            part_id=_part_id(task_id, stable_message_id, index),
            max_raw_part_bytes=max_raw_part_bytes,
            max_text_part_bytes=max_text_part_bytes,
            max_data_part_bytes=max_data_part_bytes,
        )
        total_bytes += normalized.byte_size
        _enforce_aggregate_limit(total_bytes, max_message_bytes)
        normalized_parts.append(normalized.part)
        blocks.append(normalized.model_block)

    return NormalizedA2AMessage(
        content="\n\n".join(blocks),
        parts=tuple(normalized_parts),
        message_id=stable_message_id,
        metadata=context.metadata,
        extensions=context.extensions,
        reference_task_ids=context.reference_task_ids,
    )


def _canonicalize_message_context(
    *,
    metadata: dict[str, object] | None,
    extensions: Sequence[str],
    reference_task_ids: Sequence[str],
) -> _CanonicalMessageContext:
    canonical_metadata = dict(metadata or {})
    canonical_extensions = tuple(str(value) for value in extensions)
    canonical_reference_ids = tuple(str(value) for value in reference_task_ids)
    if not (canonical_metadata or canonical_extensions or canonical_reference_ids):
        return _CanonicalMessageContext({}, (), (), None, 0)

    serialized = json.dumps(
        {
            "extensions": canonical_extensions,
            "metadata": canonical_metadata,
            "reference_task_ids": canonical_reference_ids,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return _CanonicalMessageContext(
        metadata=canonical_metadata,
        extensions=canonical_extensions,
        reference_task_ids=canonical_reference_ids,
        model_block=f"[A2A message context]\n{serialized}",
        byte_size=len(serialized.encode("utf-8")),
    )


def _normalize_part(
    part: Part,
    *,
    index: int,
    part_id: str,
    max_raw_part_bytes: int,
    max_text_part_bytes: int,
    max_data_part_bytes: int,
) -> _NormalizedPart:
    kind = part.WhichOneof("content")
    if kind is None:
        raise InvalidA2APartError(f"A2A message Part {index} has no content")

    media_type = part.media_type or None
    filename = part.filename or None
    metadata = MessageToDict(part.metadata) if part.HasField("metadata") else {}

    if kind == "text":
        value: object = part.text
        content_encoding = "utf-8"
        byte_size = len(part.text.encode("utf-8"))
        _enforce_part_limit("text", index, byte_size, max_text_part_bytes)
        model_block = (
            _annotated_text_part(
                index=index,
                text=part.text,
                media_type=media_type,
                filename=filename,
                metadata=metadata,
            )
            if metadata or media_type or filename
            else part.text
        )
    elif kind == "data":
        value = MessageToDict(part.data)
        content_encoding = "json"
        compact = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        byte_size = len(compact.encode("utf-8"))
        _enforce_part_limit("data", index, byte_size, max_data_part_bytes)
        model_block = _data_part_block(
            index=index,
            value=value,
            media_type=media_type,
            filename=filename,
            metadata=metadata,
        )
    elif kind == "raw":
        value = base64.b64encode(part.raw).decode("ascii")
        content_encoding = "base64"
        byte_size = len(part.raw)
        _enforce_part_limit("raw", index, byte_size, max_raw_part_bytes)
        model_block = None
    elif kind == "url":
        value = part.url
        content_encoding = "uri"
        byte_size = len(part.url.encode("utf-8"))
        model_block = None
    else:
        raise InvalidA2APartError(f"A2A message Part {index} has unsupported content")

    canonical_part = TaskInputPart(
        id=part_id,
        index=index,
        kind=kind,
        value=value,
        content_encoding=content_encoding,
        media_type=media_type,
        filename=filename,
        metadata=metadata,
    )
    return _NormalizedPart(
        part=canonical_part,
        model_block=model_block or _artifact_reference_block(canonical_part),
        byte_size=byte_size + _part_envelope_size(media_type, filename, metadata),
    )


def _data_part_block(
    *,
    index: int,
    value: object,
    media_type: str | None,
    filename: str | None,
    metadata: dict[str, object],
) -> str:
    rendered = json.dumps(
        {
            "filename": filename,
            "media_type": media_type,
            "metadata": metadata,
            "value": value,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"[A2A data Part {index}]\n{rendered}"


def _artifact_reference_block(part: TaskInputPart) -> str:
    details = json.dumps(
        {
            "artifact": f"/artifacts/{part.id}",
            "content_encoding": part.content_encoding,
            "filename": part.filename,
            "media_type": part.media_type,
            "metadata": part.metadata,
            "part_kind": part.kind,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"[A2A {part.kind} Part {part.index}]\n{details}"


def _part_envelope_size(
    media_type: str | None,
    filename: str | None,
    metadata: dict[str, object],
) -> int:
    serialized = json.dumps(
        {
            "filename": filename,
            "media_type": media_type,
            "metadata": metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return len(serialized.encode("utf-8"))


def _enforce_part_limit(kind: str, index: int, size: int, limit: int) -> None:
    if size > limit:
        raise InvalidA2APartError(f"A2A {kind} Part {index} exceeds the {limit}-byte limit")


def _enforce_aggregate_limit(size: int, limit: int) -> None:
    if size > limit:
        raise InvalidA2APartError(f"A2A message exceeds the {limit}-byte aggregate limit")


def _part_id(task_id: str, message_id: str, index: int) -> str:
    identity = f"cognition:a2a-input:{task_id}:{message_id}:{index}"
    return f"a2a-input-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


def _annotated_text_part(
    *,
    index: int,
    text: str,
    media_type: str | None,
    filename: str | None,
    metadata: dict[str, object],
) -> str:
    details = json.dumps(
        {
            "filename": filename,
            "media_type": media_type,
            "metadata": metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"[A2A text Part {index}]\n{details}\n\n{text}"


__all__ = [
    "InvalidA2APartError",
    "NormalizedA2AMessage",
    "normalize_a2a_parts",
]
