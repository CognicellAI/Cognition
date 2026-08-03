from __future__ import annotations

from io import BytesIO

from server.app.agent.s3_backend import S3CompatibleBackend


class FakePaginator:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803
        del Bucket
        return [
            {
                "Contents": [
                    {"Key": key, "Size": len(value)}
                    for key, value in self._objects.items()
                    if key.startswith(Prefix)
                ]
            }
        ]


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self.objects)

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: object) -> None:  # noqa: N803
        del Bucket, kwargs
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str):  # noqa: N803
        del Bucket
        if Key not in self.objects:
            error = RuntimeError("missing")
            error.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
            raise error
        return {"Body": BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        del Bucket
        self.objects.pop(Key, None)


def test_s3_backend_isolates_prefix_and_supports_file_operations() -> None:
    client = FakeS3Client()
    backend = S3CompatibleBackend(client, bucket="test", prefix="scopes/opaque")

    assert backend.write("/skills/demo/SKILL.md", "one\ntwo").error is None
    assert client.objects == {"scopes/opaque/skills/demo/SKILL.md": b"one\ntwo"}

    listing = backend.ls("/skills/")
    assert listing.entries == [{"path": "/skills/demo", "is_dir": True}]

    read = backend.read("/skills/demo/SKILL.md", offset=1, limit=1)
    assert read.file_data == {"content": "two", "encoding": "utf-8"}
    assert backend.glob("skills/**/*.md").matches == [
        {"path": "/skills/demo/SKILL.md", "is_dir": False, "size": 7, "modified_at": ""}
    ]

    edit = backend.edit("/skills/demo/SKILL.md", "two", "three")
    assert edit.error is None
    assert backend.read("/skills/demo/SKILL.md").file_data == {
        "content": "one\nthree",
        "encoding": "utf-8",
    }

    assert backend.delete("/skills/demo/SKILL.md").error is None
    assert (
        backend.read("/skills/demo/SKILL.md").error
        == "Error: File '/skills/demo/SKILL.md' not found"
    )


def test_s3_backend_never_allows_path_to_escape_prefix() -> None:
    backend = S3CompatibleBackend(FakeS3Client(), bucket="test", prefix="tenant")

    assert backend.write("/../../outside", "no").error is None
    # Normalization keeps every object below the assigned prefix.
    assert backend._key("/../../outside") == "tenant/outside"


def test_scope_prefix_is_opaque_and_stable_for_an_exact_scope() -> None:
    prefix = S3CompatibleBackend.scope_prefix(
        base_prefix="cognition",
        effective_scope={"tenant": "acme", "user": "ada"},
        hmac_key="test-key",
    )

    assert prefix.startswith("cognition/scopes/")
    assert "acme" not in prefix
    assert "ada" not in prefix
    assert prefix == S3CompatibleBackend.scope_prefix(
        base_prefix="cognition",
        effective_scope={"user": "ada", "tenant": "acme"},
        hmac_key="test-key",
    )
