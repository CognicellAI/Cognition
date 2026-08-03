"""S3-compatible Deep Agents backend.

The backend intentionally targets the S3 API rather than an AWS-specific
service.  It supports AWS S3 through the normal boto3 credential chain and
S3-compatible deployments such as Garage through an explicit endpoint URL.
"""

from __future__ import annotations

import fnmatch
import posixpath
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)


class S3CompatibleBackend(BackendProtocol):
    """Expose one S3 prefix as a safe, virtual Deep Agents filesystem.

    ``prefix`` is controlled by the runtime, not model input.  File paths are
    normalized and must remain below that prefix, preventing path traversal
    from escaping the effective-scope namespace.
    """

    def __init__(self, client: Any, bucket: str, prefix: str = "") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    @classmethod
    def from_boto3(
        cls,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        force_path_style: bool = False,
    ) -> S3CompatibleBackend:
        """Build a backend from boto3's standard AWS/S3-compatible client.

        ``force_path_style`` is useful for Garage and other local object stores;
        AWS S3 deployments can leave it disabled and use virtual-host addressing.
        Credentials deliberately come from boto3's standard provider chain.
        """
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - guarded by the s3 extra
            raise RuntimeError("Install Cognition with the 's3' extra to use S3 storage") from exc
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            config=Config(s3={"addressing_style": "path" if force_path_style else "virtual"}),
        )
        return cls(client, bucket=bucket, prefix=prefix)

    def _key(self, path: str) -> str:
        normalized = posixpath.normpath("/" + path.lstrip("/"))
        if normalized == "/.." or normalized.startswith("/../"):
            raise ValueError("path escapes the configured storage prefix")
        relative = normalized.lstrip("/")
        return "/".join(part for part in (self._prefix, relative) if part)

    def _path(self, key: str) -> str:
        if self._prefix:
            if not key.startswith(f"{self._prefix}/"):
                raise ValueError("object is outside the configured storage prefix")
            key = key[len(self._prefix) + 1 :]
        return f"/{key}"

    def _iter_objects(self, path: str = "/") -> Iterable[dict[str, Any]]:
        prefix = self._key(path).rstrip("/")
        if prefix:
            prefix += "/"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            yield from page.get("Contents", [])

    @staticmethod
    def _timestamp(value: datetime | None) -> str:
        if value is None:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()

    @staticmethod
    def _not_found(error: Exception) -> bool:
        response = getattr(error, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        return code in {"404", "NoSuchKey", "NoSuchBucket"}

    def ls(self, path: str) -> LsResult:
        try:
            entries: dict[str, FileInfo] = {}
            root = self._key(path).rstrip("/")
            prefix = f"{root}/" if root else ""
            for obj in self._iter_objects(path):
                key = str(obj["Key"])
                remainder = key[len(prefix) :] if prefix else key
                first, _, tail = remainder.partition("/")
                entry_path = f"{path.rstrip('/')}/{first}" if path != "/" else f"/{first}"
                if tail:
                    entries.setdefault(entry_path, {"path": entry_path, "is_dir": True})
                else:
                    entries[entry_path] = {
                        "path": entry_path,
                        "is_dir": False,
                        "size": int(obj.get("Size", 0)),
                        "modified_at": self._timestamp(obj.get("LastModified")),
                    }
            return LsResult(entries=[entries[key] for key in sorted(entries)])
        except Exception as exc:
            return LsResult(error=f"S3 list failed: {type(exc).__name__}")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(file_path))
            content = response["Body"].read().decode("utf-8")
        except Exception as exc:
            if self._not_found(exc):
                return ReadResult(error=f"Error: File '{file_path}' not found")
            return ReadResult(error=f"S3 read failed: {type(exc).__name__}")

        lines = content.splitlines()
        if limit <= 0:
            return ReadResult(total_lines=len(lines), no_lines_requested=True)
        selected = lines[offset : offset + limit]
        return ReadResult(
            file_data=FileData(content="\n".join(selected), encoding="utf-8"),
            total_lines=len(lines),
            start_line=offset + 1 if selected else None,
            end_line=offset + len(selected) if selected else None,
            next_offset=offset + len(selected) if offset + len(selected) < len(lines) else None,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._key(file_path),
                Body=content.encode("utf-8"),
                ContentType="text/plain; charset=utf-8",
            )
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=f"S3 write failed: {type(exc).__name__}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        result = self.read(file_path, limit=2**31 - 1)
        if result.error or result.file_data is None:
            return EditResult(error=result.error or f"Cannot read {file_path}")
        content = result.file_data["content"]
        occurrences = content.count(old_string)
        if occurrences == 0:
            return EditResult(error=f"String not found in {file_path}")
        if not replace_all:
            occurrences = 1
        updated = content.replace(old_string, new_string, occurrences)
        write = self.write(file_path, updated)
        return EditResult(
            error=write.error, path=file_path if not write.error else None, occurrences=occurrences
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            matches: list[FileInfo] = [
                {
                    "path": self._path(str(obj["Key"])),
                    "is_dir": False,
                    "size": int(obj.get("Size", 0)),
                    "modified_at": self._timestamp(obj.get("LastModified")),
                }
                for obj in self._iter_objects(path or "/")
                if fnmatch.fnmatch(self._path(str(obj["Key"])).lstrip("/"), pattern)
            ]
            return GlobResult(matches=sorted(matches, key=lambda item: item["path"]))
        except Exception as exc:
            return GlobResult(error=f"S3 glob failed: {type(exc).__name__}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        matches: list[GrepMatch] = []
        for info in self.glob(glob or "**", path).matches or []:
            read = self.read(info["path"], limit=2**31 - 1)
            if read.error or read.file_data is None:
                continue
            for line_number, line in enumerate(read.file_data["content"].splitlines(), start=1):
                if pattern in line:
                    matches.append({"path": info["path"], "line": line_number, "text": line})
                    if max_count is not None and len(matches) >= max_count:
                        return GrepResult(matches=matches, truncated=True)
        return GrepResult(matches=matches)

    def delete(self, file_path: str) -> DeleteResult:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._key(file_path))
            return DeleteResult(path=file_path)
        except Exception as exc:
            return DeleteResult(error=f"S3 delete failed: {type(exc).__name__}")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=self._key(path))
                responses.append(FileDownloadResponse(path=path, content=response["Body"].read()))
            except Exception as exc:
                error = "file_not_found" if self._not_found(exc) else "invalid_path"
                responses.append(FileDownloadResponse(path=path, error=error))
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results: list[FileUploadResponse] = []
        for path, content in files:
            try:
                self._client.put_object(Bucket=self._bucket, Key=self._key(path), Body=content)
                results.append(FileUploadResponse(path=path))
            except Exception:
                results.append(FileUploadResponse(path=path, error="invalid_path"))
        return results
