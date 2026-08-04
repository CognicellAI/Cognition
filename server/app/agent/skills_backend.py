"""Read-only Deep Agents backend for Agent-owned skill bundles."""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import (
    BackendProtocol,
    FileData,
    FileDownloadResponse,
    FileInfo,
    LsResult,
    ReadResult,
)
from deepagents.backends.utils import normalize_read_bounds

from server.app.agent.definition import AgentSkillBundle


class AgentSkillsBackend(BackendProtocol):
    """Expose immutable skill bundles from one pinned Agent definition."""

    def __init__(self, skills: list[AgentSkillBundle]) -> None:
        self._skills = {skill.name: skill.model_copy(deep=True) for skill in skills}

    def ls(self, path: str) -> LsResult:
        """List the selected Agent revision's skill directories.

        ``CompositeBackend`` removes the ``/skills/api/`` route prefix before
        calling this backend, so Deep Agents discovers the bundle directories
        from its virtual root.
        """
        if path.rstrip("/") not in {"", "/"}:
            return LsResult(entries=[])
        return LsResult(
            entries=[
                FileInfo(path=f"/{name}/", is_dir=True, size=0, modified_at="")
                for name in sorted(self._skills)
            ]
        )

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download primary or supporting skill files."""
        responses: list[FileDownloadResponse] = []
        for file_path in paths:
            parsed = _parse_bundle_path(file_path)
            if parsed is None:
                responses.append(FileDownloadResponse(path=file_path, error="invalid_path"))
                continue
            skill_name, relative_path = parsed
            skill = self._skills.get(skill_name)
            content = _bundle_content(skill, relative_path) if skill is not None else None
            if content is None:
                responses.append(FileDownloadResponse(path=file_path, error="file_not_found"))
                continue
            responses.append(
                FileDownloadResponse(
                    path=file_path,
                    content=content.encode("utf-8"),
                    error=None,
                )
            )
        return responses

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Read a raw skill file window for Deep Agents formatting middleware."""
        parsed = _parse_bundle_path(file_path)
        if parsed is None:
            return ReadResult(error=f"Invalid path {file_path}")
        skill_name, relative_path = parsed
        skill = self._skills.get(skill_name)
        content = _bundle_content(skill, relative_path) if skill is not None else None
        if content is None:
            return ReadResult(error="Skill bundle file not found")
        offset, limit = normalize_read_bounds(offset, limit)
        lines = content.splitlines()
        if limit <= 0:
            return ReadResult(no_lines_requested=True)
        else:
            selected = lines[offset : offset + limit]
        return ReadResult(
            file_data=FileData(content="\n".join(selected), encoding="utf-8"),
            total_lines=len(lines),
            start_line=offset + 1 if selected else None,
            end_line=offset + len(selected) if selected else None,
            next_offset=offset + len(selected) if offset + len(selected) < len(lines) else None,
            no_lines_requested=False,
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Avoid CompositeBackend fan-out failures for unrelated glob calls."""
        del pattern, path
        return []

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[Any]:
        """Avoid CompositeBackend fan-out failures for unrelated grep calls."""
        del pattern, path, glob
        return []


def _parse_bundle_path(path: str) -> tuple[str, str] | None:
    if not path.startswith("/"):
        return None
    parts = path.lstrip("/").split("/")
    if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts[0], "/".join(parts[1:])


def _bundle_content(skill: AgentSkillBundle | None, relative_path: str) -> str | None:
    if skill is None:
        return None
    if relative_path == "SKILL.md":
        return skill.content
    return skill.files.get(relative_path)


__all__ = ["AgentSkillsBackend"]
