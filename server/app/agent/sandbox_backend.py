"""Sandbox backend implementation for DeepAgents.

This module provides a SandboxBackend that implements the deepagents
SandboxBackendProtocol, enabling isolated command execution and file operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from deepagents.backends.protocol import (
    SandboxBackendProtocol,
    ExecuteResponse,
    FileInfo,
    WriteResult,
    EditResult,
    GrepMatch,
    FileUploadResponse,
    FileDownloadResponse,
)

from server.app.sandbox import LocalSandbox


@dataclass
class SandboxFileInfo:
    """File information for deepagents backend."""

    name: str
    path: str
    is_dir: bool
    size: int


class CognitionSandboxBackend(SandboxBackendProtocol):
    """Sandbox backend for DeepAgents using LocalSandbox.

    This backend implements the deepagents SandboxBackendProtocol,
    providing:
    - Isolated command execution via LocalSandbox
    - File operations (ls, read, write, edit)
    - Search operations (glob, grep)

    When used with create_deep_agent(), the agent gains access to an
    `execute` tool for running shell commands in the sandboxed environment.
    """

    def __init__(self, root_dir: str | Path, sandbox_id: Optional[str] = None):
        """Initialize the sandbox backend.

        Args:
            root_dir: The directory where all commands will be executed.
                      Must be an absolute path.
            sandbox_id: Optional unique identifier for this sandbox.
        """
        self._sandbox = LocalSandbox(root_dir=root_dir)
        self._id = sandbox_id or f"cognition-sandbox-{id(self)}"
        self._root_dir = Path(root_dir).resolve()

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._id

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command in the sandbox.

        Args:
            command: The shell command to execute.

        Returns:
            ExecuteResponse with output, exit code, and truncation info.
        """
        result = self._sandbox.execute(command)

        # Truncate output if too long (deepagents limit ~100KB)
        max_output_size = 100000
        truncated = len(result.output) > max_output_size
        output = result.output[:max_output_size] if truncated else result.output

        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=truncated,
        )

    # BackendProtocol implementations for file operations

    def ls_info(self, path: str = "/") -> list[FileInfo]:
        """List directory contents.

        Args:
            path: Directory path relative to workspace root.

        Returns:
            List of FileInfo objects describing files and directories.
        """
        result = self._sandbox.execute(f'ls -la "{path}"')

        file_infos = []
        for line in result.output.split("\n")[1:]:  # Skip header
            if not line.strip():
                continue

            parts = line.split()
            if len(parts) >= 9:
                is_dir = parts[0].startswith("d")
                name = " ".join(parts[8:])
                size = int(parts[4]) if parts[4].isdigit() else 0

                # Build full path
                full_path = str(self._root_dir / path.lstrip("/") / name)
                if path == "/":
                    full_path = str(self._root_dir / name)

                file_infos.append(
                    FileInfo(
                        name=name,
                        path=full_path,
                        is_dir=is_dir,
                        size=size,
                    )
                )

        return file_infos

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read file contents with line numbers.

        Args:
            file_path: Path to the file.
            offset: Line number to start from (0-indexed).
            limit: Maximum number of lines to read.

        Returns:
            File content with line numbers.
        """
        # Read with line numbers using cat -n, then filter by offset/limit
        result = self._sandbox.execute(f'cat -n "{file_path}"')

        if result.exit_code != 0:
            return f"Error reading file: {result.output}"

        lines = result.output.split("\n")

        # Filter by offset and limit
        start_idx = offset
        end_idx = offset + limit
        filtered_lines = lines[start_idx:end_idx]

        return "\n".join(filtered_lines)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write content to a file.

        Args:
            file_path: Path to the file.
            content: Content to write.

        Returns:
            WriteResult indicating success or failure.
        """
        # Escape single quotes in content
        escaped = content.replace("'", "'\"'\"'")

        # Use printf to write content
        result = self._sandbox.execute(f"printf '%s' '{escaped}' > '{file_path}'")

        if result.exit_code != 0:
            return WriteResult(
                success=False,
                message=f"Failed to write file: {result.output}",
            )

        return WriteResult(
            success=True,
            message=f"Successfully wrote to {file_path}",
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Edit a file by replacing old_string with new_string.

        Args:
            file_path: Path to the file.
            old_string: String to replace.
            new_string: Replacement string.
            replace_all: Whether to replace all occurrences.

        Returns:
            EditResult indicating success or failure with diff info.
        """
        # First read the file
        read_result = self._sandbox.execute(f'cat "{file_path}"')

        if read_result.exit_code != 0:
            return EditResult(
                success=False,
                message=f"Failed to read file for editing: {read_result.output}",
                diff=None,
            )

        original_content = read_result.output

        # Perform replacement
        if replace_all:
            new_content = original_content.replace(old_string, new_string)
        else:
            new_content = original_content.replace(old_string, new_string, 1)

        # Check if replacement happened
        if new_content == original_content:
            return EditResult(
                success=False,
                message="Old string not found in file",
                diff=None,
            )

        # Write back
        escaped = new_content.replace("'", "'\"'\"'")
        write_result = self._sandbox.execute(f"printf '%s' '{escaped}' > '{file_path}'")

        if write_result.exit_code != 0:
            return EditResult(
                success=False,
                message=f"Failed to write edited file: {write_result.output}",
                diff=None,
            )

        # Generate simple diff
        diff = f"--- {file_path}\n+++ {file_path}\n@@ -1,1 +1,1 @@\n-{old_string}\n+{new_string}"

        return EditResult(
            success=True,
            message=f"Successfully edited {file_path}",
            diff=diff,
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Find files matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g., "*.py", "**/*.json").
            path: Directory to search in.

        Returns:
            List of FileInfo objects for matching files.
        """
        # Use find command for glob-like matching
        result = self._sandbox.execute(f'find "{path}" -name "{pattern}" -type f')

        file_infos = []
        for line in result.output.strip().split("\n"):
            if not line:
                continue

            file_path = line.strip()
            file_name = Path(file_path).name

            # Get file size
            size_result = self._sandbox.execute(f'stat -c%s "{file_path}"')
            size = int(size_result.output.strip()) if size_result.exit_code == 0 else 0

            file_infos.append(
                FileInfo(
                    name=file_name,
                    path=file_path,
                    is_dir=False,
                    size=size,
                )
            )

        return file_infos

    def grep_raw(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
    ) -> list[GrepMatch] | str:
        """Search file contents using grep.

        Args:
            pattern: Search pattern.
            path: Directory to search in.
            glob: Optional glob pattern to filter files.

        Returns:
            List of GrepMatch objects or string results.
        """
        # Build grep command
        cmd = f'grep -rn "{pattern}"'

        if path:
            cmd += f' "{path}"'

        result = self._sandbox.execute(cmd)

        # Parse grep output into matches
        matches = []
        for line in result.output.strip().split("\n"):
            if not line or ":" not in line:
                continue

            # Parse format: file_path:line_num:content
            first_colon = line.find(":")
            second_colon = line.find(":", first_colon + 1)

            if first_colon > 0 and second_colon > first_colon:
                file_path = line[:first_colon]
                try:
                    line_num = int(line[first_colon + 1 : second_colon])
                    content = line[second_colon + 1 :]

                    matches.append(
                        GrepMatch(
                            file_path=file_path,
                            line_number=line_num,
                            content=content,
                        )
                    )
                except ValueError:
                    continue

        return matches if matches else result.output

    def upload_files(self, files: list[dict]) -> list[FileUploadResponse]:
        """Upload files to the sandbox (batch upload).

        Args:
            files: List of file dicts with 'path' and 'content' keys.

        Returns:
            List of FileUploadResponse for each file.
        """
        responses = []

        for file_info in files:
            path = file_info.get("path", "")
            content = file_info.get("content", "")

            write_result = self.write(path, content)

            responses.append(
                FileUploadResponse(
                    path=path,
                    success=write_result.success,
                    message=write_result.message,
                )
            )

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox (batch download).

        Args:
            paths: List of file paths to download.

        Returns:
            List of FileDownloadResponse with file content.
        """
        responses = []

        for path in paths:
            content = self.read(path)

            # Check if read failed
            if content.startswith("Error"):
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content="",
                        success=False,
                        message=content,
                    )
                )
            else:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=content,
                        success=True,
                        message="File downloaded successfully",
                    )
                )

        return responses
