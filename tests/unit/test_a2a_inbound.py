"""Tests for lossless, inert normalization of inbound A2A Parts."""

from __future__ import annotations

import json

import pytest
from a2a.helpers.proto_helpers import new_data_part
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
    assert normalized.artifacts[0].value == "aGVsbG8="
    assert normalized.artifacts[0].content_encoding == "base64"
    assert normalized.artifacts[1].value == "https://example.com/report.pdf"
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


@pytest.mark.parametrize(
    "value",
    [
        {"nested": [True, None]},
        ["proposal", 2],
        "command",
        7,
        True,
        None,
    ],
)
def test_normalizes_every_valid_a2a_data_value(value: object) -> None:
    """A2A DataPart values are any JSON value, not only objects."""
    normalized = normalize_a2a_parts(
        [new_data_part(value, media_type="application/json")],
        task_id="task-1",
        message_id="message-1",
        max_raw_part_bytes=1024,
    )

    assert json.loads(normalized.content.split("\n", 1)[1])["value"] == value
    assert normalized.artifacts == ()


def test_preserves_part_and_message_context_in_model_rendering() -> None:
    normalized = normalize_a2a_parts(
        [
            _part(
                {
                    "text": "Return a command",
                    "mediaType": "text/plain",
                    "metadata": {
                        "schema": {"type": "object"},
                        "contractVersion": "1.0",
                    },
                }
            )
        ],
        task_id="task-1",
        message_id="message-1",
        max_raw_part_bytes=1024,
        message_metadata={"roomId": "room-1"},
        message_extensions=("https://example.com/decision-room/v1",),
        reference_task_ids=("task-parent",),
    )

    assert normalized.metadata == {"roomId": "room-1"}
    assert normalized.extensions == ("https://example.com/decision-room/v1",)
    assert normalized.reference_task_ids == ("task-parent",)
    assert normalized.parts[0].media_type == "text/plain"
    assert normalized.parts[0].metadata == {
        "contractVersion": "1.0",
        "schema": {"type": "object"},
    }
    assert "A2A message context" in normalized.content
    assert "decision-room/v1" in normalized.content
    assert "contractVersion" in normalized.content
    assert "Return a command" in normalized.content


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
