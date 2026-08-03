"""ArtifactBackend: serves artifact content as virtual files.

This backend implements ``BackendProtocol`` and exposes artifacts stored in
the ``ConfigStore`` as files on virtual paths under ``/artifacts/``,
``/scratch/``, ``/contracts/``, ``/evals/``, ``/memories/``, and
``/policies/``.

When wired into the ``CompositeBackend``, Deep Agents file tools (read,
write, ls, glob, grep) can operate on artifacts as if they were real files.
"""

from __future__ import annotations

from typing import Any

import structlog
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from server.app.storage.artifact_store import ArtifactStore

logger = structlog.get_logger(__name__)

ARTIFACT_ROUTE_PREFIXES = {
    "scratch": "/scratch/",
    "artifact": "/artifacts/",
    "contract": "/contracts/",
    "eval": "/evals/",
    "memory": "/memories/",
    "policy": "/policies/",
}


class ArtifactBackend(BackendProtocol):
    """Serves ConfigStore artifacts as virtual files.

    Each artifact type maps to a virtual route. Only the latest version
    of each artifact is exposed to the agent. Write operations create new
    artifact versions automatically.

    Args:
        artifact_store: The ArtifactStore to read/write artifacts.
        scope: Scope dict for multi-tenant isolation.
    """

    def __init__(self, artifact_store: ArtifactStore, scope: dict[str, str] | None = None) -> None:
        self._store = artifact_store
        self._scope = scope

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        artifact_type, artifact_id = _parse_path(file_path)
        if artifact_id is None:
            return ReadResult(error=f"Invalid artifact path: {file_path}")

        artifact = await self._store.get_artifact(artifact_id, scope=self._scope)
        if artifact is None:
            return ReadResult(error=f"Artifact not found: {artifact_id}")

        if artifact.artifact_type != artifact_type:
            return ReadResult(
                error=f"Artifact type mismatch: expected {artifact_type}, got {artifact.artifact_type}"
            )

        content = artifact.content
        if offset > 0:
            content = content[offset:]
        if limit > 0 and len(content) > limit:
            content = content[:limit]

        return ReadResult(file_data=FileData(content=content, encoding="utf-8"))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return ReadResult(error="ArtifactBackend requires an async event loop")
        return loop.run_until_complete(self.aread(file_path, offset, limit))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        artifact_type, artifact_id = _parse_path(file_path)
        if artifact_id is None:
            return WriteResult(error=f"Invalid artifact path: {file_path}")

        from server.app.storage.config_models import ArtifactDefinition

        existing = await self._store.get_artifact(artifact_id, scope=self._scope)
        if existing is not None:
            if existing.source == "file":
                return WriteResult(error=f"Artifact '{artifact_id}' is file-managed and read-only")

            updated = ArtifactDefinition(
                id=existing.id,
                name=existing.name,
                artifact_type=existing.artifact_type,
                path=existing.path,
                content=content,
                content_type=existing.content_type,
                version=existing.version + 1,
                parent_version=existing.version,
                run_id=existing.run_id,
                checkpoint_id=existing.checkpoint_id,
                visibility=existing.visibility,
                scope=existing.scope,
                source=existing.source,
            )
            await self._store.upsert_artifact(updated)
            return WriteResult(path=file_path)
        else:
            name = artifact_id.replace("-", "_").replace(".", "_")
            new_artifact = ArtifactDefinition(
                id=artifact_id,
                name=name,
                artifact_type=artifact_type,  # type: ignore[arg-type]
                path="",
                content=content,
                content_type="text/plain",
                version=1,
                scope=self._scope or {},
                source="api",
            )
            await self._store.upsert_artifact(new_artifact)
            return WriteResult(path=file_path)

    def write(self, file_path: str, content: str) -> WriteResult:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return WriteResult(error="ArtifactBackend requires an async event loop")
        return loop.run_until_complete(self.awrite(file_path, content))

    async def als(self, path: str = "/") -> LsResult:
        path = path.rstrip("/")
        if path == "":
            path = "/"

        if path == "/":
            entries: list[FileInfo] = []
            for _artifact_type, prefix in ARTIFACT_ROUTE_PREFIXES.items():
                entries.append(FileInfo(path=prefix.rstrip("/"), is_dir=True))
            return LsResult(entries=entries)

        artifact_type = _route_prefix_to_type(path)
        if artifact_type is not None:
            artifacts = await self._store.list_artifacts(
                scope=self._scope,
                artifact_type=artifact_type,
            )
            return LsResult(
                entries=[
                    FileInfo(
                        path=f"/{artifact_type}/{a.id}",
                        size=len(a.content.encode("utf-8")),
                    )
                    for a in artifacts
                ]
            )

        return LsResult(entries=[])

    def ls(self, path: str = "/") -> LsResult:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return LsResult(entries=[])
        return loop.run_until_complete(self.als(path))

    async def als_info(self, path: str = "/") -> list[FileInfo]:
        result = await self.als(path)
        return result.entries or []

    def ls_info(self, path: str = "/") -> list[FileInfo]:
        result = self.ls(path)
        return result.entries or []

    async def aglob(self, pattern: str, path: str | None = "/") -> GlobResult:
        return GlobResult(matches=[])

    def glob(self, pattern: str, path: str | None = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        return []

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        return []

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        del pattern, path, glob, max_count
        return GrepResult(matches=[])

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        del pattern, path, glob, max_count
        return GrepResult(matches=[])

    async def agrep_raw(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> list[Any]:
        return []

    def grep_raw(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> list[Any] | str:
        return []

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results: list[FileUploadResponse] = []
        for file_path, content in files:
            try:
                text = content.decode("utf-8")
                write_result = await self.awrite(file_path, text)
                if write_result.error:
                    results.append(FileUploadResponse(path=file_path, error="is_directory"))
                else:
                    results.append(FileUploadResponse(path=file_path))
            except Exception as e:
                logger.warning("Artifact upload failed", path=file_path, error=str(e))
                results.append(FileUploadResponse(path=file_path, error="is_directory"))
        return results

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return [FileUploadResponse(path=f, error="is_directory") for f, _ in files]
        return loop.run_until_complete(self.aupload_files(files))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse] = []
        for file_path in paths:
            _, artifact_id = _parse_path(file_path)
            if artifact_id is None:
                results.append(FileDownloadResponse(path=file_path, error="invalid_path"))
                continue

            artifact = await self._store.get_artifact(artifact_id, scope=self._scope)
            if artifact is None:
                results.append(FileDownloadResponse(path=file_path, error="file_not_found"))
            else:
                results.append(
                    FileDownloadResponse(
                        path=file_path,
                        content=artifact.content.encode("utf-8"),
                    )
                )
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return [FileDownloadResponse(path=p, error="file_not_found") for p in paths]
        return loop.run_until_complete(self.adownload_files(paths))

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        read_result = await self.aread(file_path)
        if read_result.error:
            return EditResult(error=read_result.error)
        if read_result.file_data is None:
            return EditResult(error=f"Cannot read {file_path}")

        content: str = read_result.file_data["content"]
        occurrences = 0
        if replace_all:
            while old_string in content:
                content = content.replace(old_string, new_string, 1)
                occurrences += 1
        else:
            if old_string not in content:
                return EditResult(error=f"String not found in {file_path}")
            content = content.replace(old_string, new_string, 1)
            occurrences = 1

        write_result = await self.awrite(file_path, content)
        if write_result.error:
            return EditResult(error=write_result.error)
        return EditResult(path=file_path, occurrences=occurrences)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return EditResult(error="ArtifactBackend requires an async event loop")
        return loop.run_until_complete(self.aedit(file_path, old_string, new_string, replace_all))

    async def aexecute(self, command: str, *, timeout: int | None = None) -> Any:
        return None

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        return None


def _parse_path(file_path: str) -> tuple[str, str | None]:
    path = file_path.lstrip("/")
    if "/" not in path:
        return ("scratch", None)

    parts = path.split("/", 1)
    prefix = parts[0]
    artifact_id = parts[1] if len(parts) > 1 else None

    artifact_type = _route_prefix_to_type(f"/{prefix}/")
    return (artifact_type or "scratch", artifact_id)


def _route_prefix_to_type(path: str) -> str | None:
    path = path.rstrip("/")
    for atype, prefix in ARTIFACT_ROUTE_PREFIXES.items():
        if prefix.rstrip("/") == path or path == f"/{atype}":
            return atype
    return None
