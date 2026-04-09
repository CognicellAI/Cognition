"""Backend implementation for serving skills from ConfigRegistry DB."""

from __future__ import annotations

import structlog
from deepagents.backends.protocol import (
    BackendProtocol,
    FileDownloadResponse,
    FileInfo,
)
from typing import Any

from server.app.storage.config_registry import ConfigRegistry

logger = structlog.get_logger(__name__)


class ConfigRegistrySkillsBackend(BackendProtocol):
    """Backend that serves skill content from ConfigRegistry DB.

    This backend is used by SkillsMiddleware to discover and load skills.
    It maps virtual skill paths (e.g., /skills/api/web-research/SKILL.md)
    to content stored in the ConfigRegistry.
    """

    def __init__(self, registry: ConfigRegistry, scope: dict[str, str] | None = None):
        """Initialize the backend.

        Args:
            registry: The ConfigRegistry instance to query for skills.
            scope: The scope to use for skill lookups (e.g., {"user": "alice"}).
        """
        self._registry = registry
        self._scope = scope or {}

    async def als_info(self, path: str) -> list[FileInfo]:
        """List skill directories for SkillsMiddleware discovery.

        SkillsMiddleware calls this to find skill directories under each source path.
        For our /skills/api/ source, we return each skill as a directory.

        Args:
            path: The source path (typically "/" after CompositeBackend stripping).

        Returns:
            List of FileInfo dicts representing skill directories.
        """
        skills = await self._registry.list_skills(scope=self._scope)

        file_infos: list[FileInfo] = []
        for skill in skills:
            if not skill.enabled:
                continue

            # Each skill appears as a directory under the source path.
            # Return "/{skill.name}/" so CompositeBackend can re-prefix it
            # to the full virtual path (e.g. /skills/api/web-research/).
            skill_dir = f"/{skill.name}/"
            file_infos.append(
                FileInfo(
                    path=skill_dir,
                    is_dir=True,
                    size=0,
                    modified_at="",
                )
            )

        return file_infos

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download SKILL.md file content for SkillsMiddleware.

        SkillsMiddleware calls this to get the actual skill content for parsing
        YAML frontmatter.

        Args:
            paths: List of SKILL.md file paths to download.

        Returns:
            List of FileDownloadResponse with content as bytes.
        """
        responses: list[FileDownloadResponse] = []

        for file_path in paths:
            # Extract skill name from path like "/web-research/SKILL.md"
            # (CompositeBackend has already stripped the /skills/api/ prefix)
            if not file_path.startswith("/") or not file_path.endswith("/SKILL.md"):
                responses.append(FileDownloadResponse(path=file_path, error="invalid_path"))
                continue

            # Remove leading slash and /SKILL.md suffix to get skill name
            skill_name = file_path[1 : -len("/SKILL.md")]

            try:
                skill = await self._registry.get_skill(skill_name, scope=self._scope)
                if skill is None or not skill.enabled:
                    responses.append(FileDownloadResponse(path=file_path, error="file_not_found"))
                else:
                    content = skill.content or ""
                    responses.append(
                        FileDownloadResponse(
                            path=file_path,
                            content=content.encode("utf-8"),
                            error=None,
                        )
                    )
            except Exception as e:
                logger.error("skill_download_failed", path=file_path, error=str(e))
                responses.append(FileDownloadResponse(path=file_path, error="invalid_path"))

        return responses

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read SKILL.md content for progressive disclosure (sync version).

        Note: This backend primarily serves async workloads. Use aread() instead.

        Args:
            file_path: The SKILL.md file path to read.
            offset: Line offset to start reading from (0-indexed).
            limit: Maximum number of lines to read.

        Returns:
            Error message directing to use async version.
        """
        return "Error: Use aread() for ConfigRegistrySkillsBackend"

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read SKILL.md content for progressive disclosure (async version).

        Called when LLM uses file tools to read the full skill content
        after seeing the progressive disclosure hint in system prompt.

        Args:
            file_path: The SKILL.md file path to read.
            offset: Line offset to start reading from (0-indexed).
            limit: Maximum number of lines to read.

        Returns:
            Formatted file content with line numbers, or error message.
        """
        # Reuse the same logic as adownload_files but return formatted string
        if not file_path.startswith("/") or not file_path.endswith("/SKILL.md"):
            return f"Error: Invalid path {file_path}"

        skill_name = file_path[1 : -len("/SKILL.md")]

        try:
            skill = await self._registry.get_skill(skill_name, scope=self._scope)
            if skill is None or not skill.enabled:
                return f"Error: Skill not found: {skill_name}"

            content = skill.content or ""
            lines = content.splitlines()

            # Apply offset and limit
            start = offset
            end = min(offset + limit, len(lines))
            selected_lines = lines[start:end]

            # Format with line numbers (1-indexed, offset-adjusted)
            formatted_lines = []
            for i, line in enumerate(selected_lines):
                line_num = start + i + 1  # 1-indexed line numbers
                formatted_lines.append(f"{line_num:6}\t{line}")

            return "\n".join(formatted_lines)

        except Exception as e:
            return f"Error reading skill {skill_name}: {str(e)}"

    def ls_info(self, path: str) -> list[FileInfo]:
        """Synchronous wrapper for skill directory listing.

        CompositeBackend uses synchronous file tool methods. Provide a sync wrapper
        around the async implementation so routed skill paths participate cleanly
        in mixed backend operations.
        """
        import asyncio

        return asyncio.run(self.als_info(path))

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Return no matches for glob over skill routes.

        Skills are only exposed as concrete directories and SKILL.md files. Globbing
        across the routed skill backend should not raise NotImplementedError because
        CompositeBackend queries all backends during default-path glob operations.
        """
        return []

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[Any]:
        """Return no matches for grep over skill routes.

        This keeps mixed backend grep operations from failing when CompositeBackend
        fans out across routes that are unrelated to the repo filesystem search.
        """
        return []

    # Remaining BackendProtocol methods (glob_info, write, edit, upload_files, etc.)
    # are intentionally not implemented. SkillsMiddleware only needs als_info,
    # adownload_files, and aread. All other paths are handled by the default
    # sandbox backend via CompositeBackend routing.
