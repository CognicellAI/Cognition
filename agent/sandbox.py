"""Local sandbox backend for agent container.

This module provides a SandboxBackendProtocol implementation by subclassing
BaseSandbox from deepagents. The container IS the sandbox, so local execution
is safe.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import structlog
from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse

logger = structlog.get_logger()


class LocalSandboxBackend(BaseSandbox):
    """Sandbox backend that executes commands locally inside the container.

    Inherits from BaseSandbox which provides default implementations for:
    - ls_info, read, grep_raw, glob_info, write, edit (all via execute())

    We only need to implement:
    - execute(): Run shell commands
    - id: Unique sandbox identifier
    - upload_files(): Write files to workspace
    - download_files(): Read files from workspace
    """

    def __init__(self, workspace_path: str = "/workspace/repo") -> None:
        self.workspace_path = Path(workspace_path)
        self._id = os.environ.get("SESSION_ID", "local")

        logger.info(
            "LocalSandboxBackend initialized",
            workspace=str(self.workspace_path),
            sandbox_id=self._id,
        )

    @property
    def id(self) -> str:
        """Unique identifier for this sandbox."""
        return self._id

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a shell command in the workspace directory."""
        logger.debug("Executing command", command=command[:100])

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self.workspace_path,
            )

            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr if output else result.stderr

            return ExecuteResponse(output=output, exit_code=result.returncode)

        except subprocess.TimeoutExpired:
            return ExecuteResponse(output="Command timed out after 300 seconds", exit_code=-1)
        except Exception as e:
            return ExecuteResponse(output=f"Execution failed: {e}", exit_code=-1)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the workspace."""
        responses: list[FileUploadResponse] = []
        for file_path, content in files:
            try:
                full_path = self.workspace_path / file_path.lstrip("/")
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_bytes(content)
                responses.append(FileUploadResponse(path=file_path, error=None))
                logger.debug("Uploaded file", path=file_path, size=len(content))
            except Exception as e:
                responses.append(FileUploadResponse(path=file_path, error=str(e)))
                logger.error("Failed to upload file", path=file_path, error=str(e))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the workspace."""
        responses: list[FileDownloadResponse] = []
        for file_path in paths:
            try:
                full_path = self.workspace_path / file_path.lstrip("/")
                if not full_path.exists():
                    responses.append(
                        FileDownloadResponse(path=file_path, content=None, error="file_not_found")
                    )
                    continue
                content = full_path.read_bytes()
                responses.append(FileDownloadResponse(path=file_path, content=content, error=None))
                logger.debug("Downloaded file", path=file_path, size=len(content))
            except Exception as e:
                responses.append(FileDownloadResponse(path=file_path, content=None, error=str(e)))
                logger.error("Failed to download file", path=file_path, error=str(e))
        return responses
