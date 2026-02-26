"""Sandbox backend implementations for DeepAgents.

This module provides sandbox backends that combine:
1. Native filesystem operations via deepagents.backends.FilesystemBackend
2. Command execution via LocalShellBackend (local) or DockerExecutionBackend (Docker)

The local backend is used for development; the Docker backend provides
kernel-level isolation per session for production.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

import structlog
from deepagents.backends import FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    SandboxBackendProtocol,
)

logger = structlog.get_logger(__name__)


class CognitionLocalSandboxBackend(LocalShellBackend, SandboxBackendProtocol):
    """Local sandbox backend combining native file ops with shell execution.

    Inherits from LocalShellBackend for robust, OS-compatible file operations:
    - ls_info, read, write, edit, glob_info, grep_raw, upload/download

    Overrides execute() to use shlex.split() + shell=False for security:
    - Uses shlex.split() to parse command arguments safely
    - Uses shell=False to prevent shell injection attacks
    - Inherits virtual_mode support from parent class

    Security features:
    - Protected paths (e.g., .cognition/) cannot be written to or executed in
    - Path confinement via virtual_mode (inherited from parent)
    """

    def __init__(
        self,
        root_dir: str | Path,
        sandbox_id: str | None = None,
        protected_paths: list[str] | None = None,
    ):
        """Initialize the local sandbox backend.

        Args:
            root_dir: The directory where all commands will be executed.
                      Must be an absolute path.
            sandbox_id: Optional unique identifier for this sandbox.
            protected_paths: List of protected path prefixes (relative to workspace).
                           Defaults to [".cognition"].
        """
        # Initialize parent LocalShellBackend with virtual_mode=True for security
        super().__init__(root_dir=root_dir, virtual_mode=True)
        self._id = sandbox_id or f"cognition-local-{id(self)}"
        self._protected_paths = protected_paths or [".cognition"]

    def _is_protected_path(self, path: str) -> bool:
        """Check if a path is protected.

        Args:
            path: The path to check (relative or absolute).

        Returns:
            True if the path is protected, False otherwise.
        """
        # Resolve the path to check if it's under any protected prefix
        try:
            resolved = self._resolve_path(path)
            resolved_str = str(resolved)
            for protected in self._protected_paths:
                # Check if the protected path is a prefix of the resolved path
                protected_full = (self.cwd / protected).resolve()
                if resolved_str.startswith(str(protected_full)):
                    return True
        except ValueError:
            # If path resolution fails, be conservative and allow it
            # (the parent class will handle the error)
            pass
        return False

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._id

    def write(self, file_path: str, content: str) -> Any:
        """Write content to file with protected path check.

        Args:
            file_path: File path to write to.
            content: Content to write.

        Returns:
            WriteResult from the parent class.

        Raises:
            PermissionError: If the path is protected.
        """
        if self._is_protected_path(file_path):
            raise PermissionError(f"Writing to protected path is not allowed: {file_path}")
        return super().write(file_path, content)

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command in the sandbox using shell=False for security.

        This overrides the parent class to use shlex.split() + shell=False
        instead of shell=True, preventing shell injection attacks.
        Also checks for protected path access in the command.
        """
        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        # Check for protected path access in the command
        for protected in self._protected_paths:
            if protected in command:
                return ExecuteResponse(
                    output=f"Error: Command attempts to access protected path: {protected}",
                    exit_code=1,
                    truncated=False,
                )

        try:
            # Parse command using shlex for safe argument splitting
            cmd_args = shlex.split(command)

            result = subprocess.run(
                cmd_args,
                shell=False,  # Security: no shell execution, prevents injection
                capture_output=True,
                text=True,
                cwd=str(self.cwd),
                timeout=self._timeout,
                env=self._env,
            )

            # Combine stdout and stderr
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                output_parts.extend(f"[stderr] {line}" for line in stderr_lines)

            output = "\n".join(output_parts) if output_parts else "<no output>"

            # Check for truncation
            truncated = False
            if len(output) > self._max_output_bytes:
                output = output[: self._max_output_bytes]
                output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
                truncated = True

            # Add exit code info if non-zero
            if result.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: Command timed out after {self._timeout:.1f} seconds.",
                exit_code=124,
                truncated=False,
            )
        except Exception as e:
            return ExecuteResponse(
                output=f"Error executing command: {e}",
                exit_code=1,
                truncated=False,
            )


class CognitionDockerSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """Docker sandbox backend with filesystem file ops and containerized execution.

    Uses FilesystemBackend for file operations (workspace is volume-mounted,
    so files are shared between host and container). Routes command execution
    through DockerExecutionBackend for kernel-level isolation.

    This provides:
    - Fast file I/O via direct filesystem access (no docker cp overhead)
    - Isolated command execution inside a per-session container
    - Resource limits (CPU, memory) on executed commands
    - Optional network isolation
    """

    def __init__(
        self,
        root_dir: str | Path,
        sandbox_id: str | None = None,
        image: str = "cognition-sandbox:latest",
        network_mode: str = "none",
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        host_workspace: str = "",
    ):
        """Initialize the Docker sandbox backend.

        Args:
            root_dir: Workspace directory (container-internal path for file ops).
            sandbox_id: Unique identifier for this sandbox/container.
            image: Docker image to use for sandbox containers.
            network_mode: Docker network mode ("none", "bridge", or network name).
            memory_limit: Container memory limit (e.g., "512m", "1g").
            cpu_limit: CPU core limit (e.g., 1.0 = one core).
            host_workspace: Host filesystem path for Docker volume mount.
                Required when Cognition runs inside Docker (sibling containers).
        """
        # FilesystemBackend for file operations with virtual_mode=True for security
        super().__init__(root_dir=root_dir, virtual_mode=True)

        self._id = sandbox_id or f"cognition-docker-{id(self)}"
        self._image = image
        self._network_mode = network_mode
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._host_workspace = host_workspace

        # Lazy-init the Docker execution backend
        self._docker_backend: object | None = None

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._id

    def _get_docker_backend(self) -> object:
        """Lazily initialize the DockerExecutionBackend.

        Returns:
            DockerExecutionBackend instance.

        Raises:
            RuntimeError: If Docker is not available.
        """
        if self._docker_backend is None:
            from server.app.execution.backend import DockerExecutionBackend

            self._docker_backend = DockerExecutionBackend(
                root_dir=self.cwd,
                sandbox_id=self._id,
                image=self._image,
                network_mode=self._network_mode,
                memory_limit=self._memory_limit,
                cpu_limit=self._cpu_limit,
                host_workspace=self._host_workspace,
            )
            logger.info(
                "Docker sandbox backend initialized",
                sandbox_id=self._id,
                image=self._image,
                network_mode=self._network_mode,
            )
        return self._docker_backend

    def execute(self, command: str) -> ExecuteResponse:
        """Execute a command inside the Docker container.

        Commands are run via docker exec_run inside an isolated container.
        The workspace directory is volume-mounted so file changes are visible.
        """
        docker_backend = self._get_docker_backend()
        result = docker_backend.execute(command)  # type: ignore[union-attr]

        return ExecuteResponse(
            output=result.output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )


def create_sandbox_backend(
    root_dir: str | Path,
    sandbox_id: str | None = None,
    sandbox_backend: str = "local",
    docker_image: str = "cognition-sandbox:latest",
    docker_network: str = "none",
    docker_memory_limit: str = "512m",
    docker_cpu_limit: float = 1.0,
    docker_host_workspace: str = "",
) -> FilesystemBackend:
    """Factory for creating sandbox backends from settings.

    Args:
        root_dir: Workspace root directory.
        sandbox_id: Unique identifier for the sandbox.
        sandbox_backend: Backend type - "local" or "docker".
        docker_image: Docker image for sandbox containers.
        docker_network: Docker network mode.
        docker_memory_limit: Container memory limit.
        docker_cpu_limit: Container CPU limit.
        docker_host_workspace: Host filesystem path for Docker volume mount.

    Returns:
        A sandbox backend implementing both FilesystemBackend and
        SandboxBackendProtocol.

    Raises:
        ValueError: If sandbox_backend is not "local" or "docker".
    """
    if sandbox_backend == "local":
        return CognitionLocalSandboxBackend(
            root_dir=root_dir,
            sandbox_id=sandbox_id,
        )
    elif sandbox_backend == "docker":
        return CognitionDockerSandboxBackend(
            root_dir=root_dir,
            sandbox_id=sandbox_id,
            image=docker_image,
            network_mode=docker_network,
            memory_limit=docker_memory_limit,
            cpu_limit=docker_cpu_limit,
            host_workspace=docker_host_workspace,
        )
    else:
        raise ValueError(
            f"Unknown sandbox_backend: {sandbox_backend!r}. Must be 'local' or 'docker'."
        )
