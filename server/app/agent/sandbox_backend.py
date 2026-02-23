"""Sandbox backend implementations for DeepAgents.

This module provides sandbox backends that combine:
1. Native filesystem operations via deepagents.backends.FilesystemBackend
2. Command execution via LocalSandbox (local) or DockerExecutionBackend (Docker)

The local backend is used for development; the Docker backend provides
kernel-level isolation per session for production.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    SandboxBackendProtocol,
)

from server.app.execution.sandbox import LocalSandbox

logger = structlog.get_logger(__name__)


class CognitionLocalSandboxBackend(FilesystemBackend, SandboxBackendProtocol):
    """Local sandbox backend combining native file ops with shell execution.

    Inherits from FilesystemBackend for robust, OS-compatible file operations:
    - ls_info, read, write, edit, glob_info, grep_raw, upload/download

    Implements SandboxBackendProtocol by adding execute():
    - Uses LocalSandbox to run shell commands in the workspace root
    - Captures stdout/stderr with truncation limits
    """

    def __init__(self, root_dir: str | Path, sandbox_id: str | None = None):
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
        # FilesystemBackend for file operations (direct host filesystem access)
        super().__init__(root_dir=root_dir)

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

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to root_dir, handling leading slashes."""
        clean_path = path.lstrip("/")
        full_path = (self.cwd / clean_path).resolve()

        if not str(full_path).startswith(str(self.cwd)):
            raise ValueError(f"Path '{path}' escapes sandbox root")

        return full_path

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
