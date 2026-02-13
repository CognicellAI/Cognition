"""Local sandbox backend implementation for DeepAgents.

This module provides a CognitionLocalSandboxBackend that combines:
1. Native filesystem operations via deepagents.backends.FilesystemBackend (robust, cross-platform)
2. Isolated command execution via LocalSandbox (safe shell execution)

This hybrid approach ensures best performance and compatibility on the local host
while providing the shell execution capabilities agents need.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    SandboxBackendProtocol,
    ExecuteResponse,
)

from server.app.sandbox import LocalSandbox


class CognitionLocalSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """Local sandbox backend combining native file ops with shell execution.

    Inherits from FilesystemBackend for robust, OS-compatible file operations:
    - ls_info, read, write, edit, glob_info, grep_raw, upload/download

    Implements SandboxBackendProtocol by adding execute():
    - Uses LocalSandbox to run shell commands in the workspace root
    - Captures stdout/stderr with truncation limits
    """

    def __init__(self, root_dir: str | Path, sandbox_id: Optional[str] = None):
        """Initialize the local sandbox backend.

        Args:
            root_dir: The directory where all commands will be executed.
                      Must be an absolute path.
            sandbox_id: Optional unique identifier for this sandbox.
        """
        # Initialize parent FilesystemBackend for file operations
        super().__init__(root_dir=root_dir)

        # Initialize LocalSandbox for command execution
        self._sandbox = LocalSandbox(root_dir=root_dir)
        self._id = sandbox_id or f"cognition-local-{id(self)}"

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._id

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to root_dir, handling leading slashes."""
        # Strip leading slashes to treat absolute paths as relative to root_dir
        clean_path = path.lstrip("/")
        full_path = (self.cwd / clean_path).resolve()

        # Security check: ensure path is within root_dir
        if not str(full_path).startswith(str(self.cwd)):
            raise ValueError(f"Path '{path}' escapes sandbox root")

        return full_path

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command in the sandbox using LocalSandbox.

        This overrides the protocol definition to provide actual shell execution.
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
