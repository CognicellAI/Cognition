"""Minimal S3-compatible durable object client for Cognition persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
from typing import Any


class S3ObjectStore:
    """Store opaque, scoped artifact bodies without exposing a filesystem API."""

    def __init__(self, client: Any, *, bucket: str, base_prefix: str, hmac_key: str) -> None:
        self._client = client
        self._bucket = bucket
        self._base_prefix = base_prefix.strip("/")
        self._hmac_key = hmac_key

    @classmethod
    def from_boto3(
        cls,
        *,
        bucket: str,
        base_prefix: str,
        hmac_key: str,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        force_path_style: bool = False,
    ) -> S3ObjectStore:
        """Create a client using boto3's standard credential provider chain."""
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - guarded by the s3 extra
            raise RuntimeError("Install Cognition with the 's3' extra to use S3 storage") from exc
        return cls(
            boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                config=Config(s3={"addressing_style": "path" if force_path_style else "virtual"}),
            ),
            bucket=bucket,
            base_prefix=base_prefix,
            hmac_key=hmac_key,
        )

    def scoped_key(self, scope: dict[str, str], path: str) -> str:
        """Return a deterministic opaque key for one exact scope and body path."""
        normalized = posixpath.normpath("/" + path.lstrip("/"))
        if normalized == "/.." or normalized.startswith("/../"):
            raise ValueError("durable object path escapes its namespace")
        canonical_scope = json.dumps(
            scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        scope_digest = hmac.new(
            self._hmac_key.encode("utf-8"), canonical_scope, hashlib.sha256
        ).hexdigest()
        return "/".join(
            part
            for part in (
                self._base_prefix,
                "scopes",
                scope_digest,
                normalized.lstrip("/"),
            )
            if part
        )

    def verify_connection(self) -> None:
        """Fail clearly when the selected bucket is unavailable."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception as exc:
            raise RuntimeError(
                f"Configured S3-compatible storage is unavailable: {type(exc).__name__}"
            ) from exc

    def put(self, key: str, body: bytes) -> None:
        """Write one immutable content-addressed body."""
        self._client.put_object(Bucket=self._bucket, Key=key, Body=body)

    def get(self, key: str) -> bytes:
        """Read one durable body."""
        return bytes(self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read())

    def delete(self, key: str) -> None:
        """Delete one durable body during artifact lifecycle cleanup."""
        self._client.delete_object(Bucket=self._bucket, Key=key)


__all__ = ["S3ObjectStore"]
