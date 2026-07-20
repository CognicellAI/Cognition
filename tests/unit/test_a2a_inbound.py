"""Tests for lossless, inert normalization of inbound A2A Parts."""

from __future__ import annotations

import json

import pytest
from a2a.types import Part
from google.protobuf.json_format import ParseDict  # type: ignore[import-untyped]

from server.app.protocols.a2a.inbound import InvalidA2APartError, normalize_a2a_parts


def _part(value: dict) -> Part:
    part = Part()
    ParseDict(value, part)
    return part


def test_normalizes_all_part_variants_in_wire_order_without_fetching() -> None:
    normalized = normalize_a2a_parts(
        [
            _part({"text": "Analyze"}),
            _part({"data": {"priority": 3}, "mediaType": "application/json"}),
            _part(
                {
                    "raw": "aGVsbG8=",
                    "filename": "note.txt",
                    "mediaType": "text/plain",
                }
            ),
            _part(
                {
                    "url": "https://example.com/report.pdf",
                    "filename": "report.pdf",
                    "mediaType": "application/pdf",
                }
            ),
        ],
        task_id="task-1",
        message_id="message-1",
        max_raw_part_bytes=1024,
    )

    assert normalized.content.index("Analyze") < normalized.content.index("A2A data Part 1")
    assert normalized.content.index("A2A data Part 1") < normalized.content.index("A2A raw Part 2")
    assert normalized.content.index("A2A raw Part 2") < normalized.content.index("A2A url Part 3")
    assert json.dumps({"priority": 3.0}, sort_keys=True) in normalized.content
    assert [artifact.kind for artifact in normalized.artifacts] == ["raw", "url"]
    assert normalized.artifacts[0].content == "aGVsbG8="
    assert normalized.artifacts[0].content_encoding == "base64"
    assert normalized.artifacts[1].content == "https://example.com/report.pdf"
    assert normalized.artifacts[1].content_encoding == "uri"
    assert all(f"/artifacts/{item.id}" in normalized.content for item in normalized.artifacts)


def test_artifact_ids_are_idempotent_per_message_and_unique_across_messages() -> None:
    part = _part({"raw": "aGVsbG8="})
    first = normalize_a2a_parts(
        [part], task_id="task-1", message_id="message-1", max_raw_part_bytes=10
    )
    retry = normalize_a2a_parts(
        [part], task_id="task-1", message_id="message-1", max_raw_part_bytes=10
    )
    continuation = normalize_a2a_parts(
        [part], task_id="task-1", message_id="message-2", max_raw_part_bytes=10
    )

    assert first.artifacts[0].id == retry.artifacts[0].id
    assert first.artifacts[0].id != continuation.artifacts[0].id


def test_rejects_unset_content_and_oversized_raw_parts() -> None:
    with pytest.raises(InvalidA2APartError, match="has no content"):
        normalize_a2a_parts([Part()], task_id="task", message_id="message", max_raw_part_bytes=10)

    with pytest.raises(InvalidA2APartError, match="exceeds the 4-byte limit"):
        normalize_a2a_parts(
            [_part({"raw": "aGVsbG8="})],
            task_id="task",
            message_id="message",
            max_raw_part_bytes=4,
        )
