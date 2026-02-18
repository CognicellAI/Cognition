"""Execution backend protocol and implementations for Cognition.

Provides a Cognition-owned interface for sandbox backends with:
- ExecutionBackend protocol (Cognition-owned)
- Adapter to deepagents.SandboxBackendProtocol
- Local and Docker backend implementations
- Factory for backend creation

This module enables pluggable execution backends while maintaining
Cognition's architectural independence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ExecutionResult:
    """Result of command execution.

    Attributes:
        output: Combined stdout and stderr
        exit_code: Process exit code (0 for success)
        truncated: Whether output was truncated
    """

    output: str
    exit_code: int
    truncated: bool = False


@runtime_checkable
class ExecutionBackend(Protocol):
    """Cognition-owned protocol for execution backends.

    This protocol defines the interface that all Cognition execution
    backends must implement. It is independent of deepagents and
    provides a clean abstraction for:
    - Command execution
    - File operations
    - Lifecycle management

    Implementations:
    - LocalExecutionBackend: Local subprocess execution
    - DockerExecutionBackend: Container-per-session (P1-4)
    - CloudExecutionBackend: ECS/Lambda (P3-3)

    Example:
        class MyBackend:
            def execute(self, command: str, timeout: float | None = None) -> ExecutionResult:
                # Implementation
                pass

            def read_file(self, path: str) -> str:
                # Implementation
                pass

            # ... other methods
    """

    @abstractmethod
    def execute(self, command: str, timeout: float | None = None) -> ExecutionResult:
        """Execute a command.

        Args:
            command: Command string to execute
            timeout: Optional timeout in seconds

        Returns:
            Execution result with output and exit code
        """
        ...

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read file contents.

        Args:
            path: Path to file (relative or absolute)

        Returns:
            File contents as string

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write content to file.

        Args:
            path: Path to file (relative or absolute)
            content: Content to write

        Raises:
            IOError: If write fails
        """
        ...

    @abstractmethod
    def list_files(self, path: str = ".") -> list[dict[str, Any]]:
        """List files in directory.

        Args:
            path: Directory path (relative or absolute)

        Returns:
            List of file info dictionaries with keys:
            - path: File path
            - is_dir: Whether it's a directory
            - size: File size in bytes
        """
        ...


class LocalExecutionBackend:
    """Local execution backend using subprocess.

    Provides command execution and file operations on the local
    filesystem. Uses LocalSandbox for commands and direct file
    operations for efficiency.

    This is the default backend for development.

    Attributes:
        root_dir: Root directory for all operations
        sandbox_id: Unique identifier for this backend instance
    """

    def __init__(self, root_dir: str | Path, sandbox_id: str | None = None):
        """Initialize local execution backend.

        Args:
            root_dir: Root directory for execution
            sandbox_id: Optional unique identifier
        """
        from server.app.execution.sandbox import LocalSandbox

        self.root_dir = Path(root_dir).resolve()
        self.sandbox_id = sandbox_id or f"local-{id(self)}"
        self._sandbox = LocalSandbox(root_dir=self.root_dir)

    def execute(self, command: str, timeout: float | None = 300.0) -> ExecutionResult:
        """Execute command via LocalSandbox.

        Args:
            command: Command string
            timeout: Timeout in seconds (default 300)

        Returns:
            Execution result
        """
        result = self._sandbox.execute(command, timeout=timeout)

        # Determine if output was truncated
        max_output_size = 100000
        truncated = len(result.output) > max_output_size
        output = result.output[:max_output_size] if truncated else result.output

        return ExecutionResult(
            output=output,
            exit_code=result.exit_code,
            truncated=truncated,
        )

    def read_file(self, path: str) -> str:
        """Read file contents.

        Args:
            path: File path (relative to root_dir)

        Returns:
            File contents

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        full_path = self._resolve_path(path)

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        return full_path.read_text()

    def write_file(self, path: str, content: str) -> None:
        """Write content to file.

        Args:
            path: File path (relative to root_dir)
            content: Content to write

        Raises:
            IOError: If write fails
        """
        full_path = self._resolve_path(path)

        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)

        full_path.write_text(content)

    def list_files(self, path: str = ".") -> list[dict[str, Any]]:
        """List files in directory.

        Args:
            path: Directory path (relative to root_dir)

        Returns:
            List of file info dictionaries
        """
        full_path = self._resolve_path(path)

        if not full_path.exists():
            return []

        files = []
        for item in full_path.iterdir():
            files.append(
                {
                    "path": str(item.relative_to(self.root_dir)),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                }
            )

        return files

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to root_dir.

        Args:
            path: Path string

        Returns:
            Absolute Path object

        Raises:
            ValueError: If path escapes root_dir
        """
        # Handle absolute-looking paths as relative to root
        clean_path = path.lstrip("/")
        full_path = (self.root_dir / clean_path).resolve()

        # Security check: ensure within root_dir
        if not str(full_path).startswith(str(self.root_dir)):
            raise ValueError(f"Path '{path}' escapes sandbox root")

        return full_path


class ExecutionBackendAdapter:
    """Adapter from ExecutionBackend to deepagents.SandboxBackendProtocol.

        Wraps an ExecutionBackend to provide the interface expected by
    deepagents while keeping Cognition's execution layer independent.

    This adapter allows deepagents to use Cognition's pluggable backends
    without direct dependencies.

    Attributes:
        backend: Wrapped ExecutionBackend instance
    """

    def __init__(self, backend: ExecutionBackend):
        """Initialize adapter with execution backend.

        Args:
            backend: ExecutionBackend to wrap
        """
        self.backend = backend

    @property
    def id(self) -> str:
        """Return unique identifier."""
        return getattr(self.backend, "sandbox_id", str(id(self.backend)))

    def execute(self, command: str) -> Any:
        """Execute command via wrapped backend.

        Args:
            command: Command string

        Returns:
            ExecuteResponse compatible with deepagents
        """
        from deepagents.backends.protocol import ExecuteResponse

        result = self.backend.execute(command)

        return ExecuteResponse(
            output=result.output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    def read(self, path: str) -> str:
        """Read file via wrapped backend."""
        return self.backend.read_file(path)

    def write(self, path: str, content: str) -> None:
        """Write file via wrapped backend."""
        return self.backend.write_file(path, content)

    def glob(self, pattern: str) -> list[str]:
        """Glob files via wrapped backend."""
        import fnmatch

        all_files = []
        for file_info in self.backend.list_files("."):
            if fnmatch.fnmatch(file_info["path"], pattern):
                all_files.append(file_info["path"])

        return all_files

    def grep(self, pattern: str, path: str) -> list[dict[str, Any]]:
        """Search files via wrapped backend."""
        results = []
        for file_info in self.backend.list_files(path):
            if not file_info["is_dir"]:
                try:
                    content = self.backend.read_file(file_info["path"])
                    for i, line in enumerate(content.split("\n"), 1):
                        if pattern in line:
                            results.append(
                                {
                                    "file": file_info["path"],
                                    "line": i,
                                    "content": line,
                                }
                            )
                except Exception:
                    pass

        return results


def create_execution_backend(
    backend_type: str,
    root_dir: str | Path,
    sandbox_id: str | None = None,
    **kwargs: Any,
) -> ExecutionBackend:
    """Factory for creating execution backends.

    Args:
        backend_type: Type of backend ("local", "docker")
        root_dir: Root directory for operations
        sandbox_id: Optional unique identifier
        **kwargs: Backend-specific configuration

    Returns:
        Configured ExecutionBackend instance

    Raises:
        ValueError: If backend_type is unknown

    Example:
        backend = create_execution_backend(
            backend_type="local",
            root_dir="/path/to/project",
            sandbox_id="session-123",
        )

        result = backend.execute("ls -la")
    """
    if backend_type == "local":
        return LocalExecutionBackend(root_dir, sandbox_id)
    elif backend_type == "docker":
        return DockerExecutionBackend(
            root_dir=root_dir,
            sandbox_id=sandbox_id,
            image=kwargs.get("docker_image", "cognition-sandbox:latest"),
            network_mode=kwargs.get("docker_network_mode", "none"),
            memory_limit=kwargs.get("docker_memory_limit", "512m"),
            cpu_limit=kwargs.get("docker_cpu_limit", 1.0),
            host_workspace=kwargs.get("docker_host_workspace", ""),
        )
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")


class DockerExecutionBackend:
    """Docker container execution backend.

    Provides isolated execution environment using Docker containers.
    Each session gets its own container with:
    - Kernel-level isolation via namespaces
    - Resource limits (CPU, memory)
    - Network isolation (configurable)
    - Volume mounting for workspace persistence

    This backend is suitable for production and semi-trusted code.
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
        """Initialize Docker execution backend.

        Args:
            root_dir: Workspace directory (container-internal path for file ops)
            sandbox_id: Unique identifier for this container
            image: Docker image name
            network_mode: Docker network mode ("none", "bridge", etc.)
            memory_limit: Memory limit (e.g., "512m", "1g")
            cpu_limit: CPU core limit
            host_workspace: Host filesystem path for Docker volume mount.
                If empty, root_dir is used (assumes local execution).
                Required when Cognition itself runs in a container
                and spawns sibling sandbox containers.
        """
        self.root_dir = Path(root_dir).resolve()
        self.sandbox_id = sandbox_id or f"docker-{id(self)}"
        self.image = image
        self.network_mode = network_mode
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.host_workspace = host_workspace or str(self.root_dir)
        self._container = None

    def _ensure_container(self):
        """Ensure container is running."""
        import docker
        import subprocess

        if self._container is None:
            client = docker.from_env()
            container_name = f"cognition-{self.sandbox_id}"

            # Check if container already exists
            try:
                existing = client.containers.get(container_name)
                if existing.status == "running":
                    self._container = existing
                    return
                else:
                    existing.remove(force=True)
            except Exception:
                pass

            # Create and start new container with security hardening:
            # - cap_drop=ALL: Remove all Linux capabilities
            # - security_opt=no-new-privileges: Prevent privilege escalation
            # - read_only=True: Read-only root filesystem
            # - tmpfs /tmp: Writable temp directory on tmpfs
            self._container = client.containers.run(
                self.image,
                name=container_name,
                detach=True,
                network_mode=self.network_mode,
                mem_limit=self.memory_limit,
                cpu_quota=int(self.cpu_limit * 100000),
                volumes={self.host_workspace: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                stdin_open=True,
                tty=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                read_only=True,
                tmpfs={"/tmp": "size=64m", "/home": "size=16m"},
                labels={"cognition.sandbox.id": self.sandbox_id, "cognition.managed": "true"},
            )

    def execute(self, command: str, timeout: float | None = 300.0):
        """Execute command in Docker container."""
        import structlog

        logger = structlog.get_logger(__name__)

        self._ensure_container()
        try:
            exit_code, output = self._container.exec_run(
                cmd=["sh", "-c", command],
                workdir="/workspace",
            )

            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")

            max_size = 100000
            truncated = len(output) > max_size
            output = output[:max_size] if truncated else output

            return ExecutionResult(output=output, exit_code=exit_code, truncated=truncated)
        except Exception as e:
            logger.error("Docker execution failed", error=str(e))
            return ExecutionResult(output=f"Error: {e}", exit_code=-1, truncated=False)

    def read_file(self, path: str) -> str:
        """Read file from container."""
        self._ensure_container()
        import tempfile
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_file = Path(tmpdir) / "file"
            subprocess.run(
                ["docker", "cp", f"{self._container.id}:/workspace/{path}", str(temp_file)],
                check=True,
                capture_output=True,
            )
            return temp_file.read_text()

    def write_file(self, path: str, content: str) -> None:
        """Write file to container."""
        self._ensure_container()
        import tempfile
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            temp_file = Path(tmpdir) / "file"
            temp_file.write_text(content)
            dir_path = Path(path).parent
            if str(dir_path) != ".":
                self.execute(f"mkdir -p /workspace/{dir_path}")
            subprocess.run(
                ["docker", "cp", str(temp_file), f"{self._container.id}:/workspace/{path}"],
                check=True,
                capture_output=True,
            )

    def list_files(self, path: str = ".") -> list[dict]:
        """List files in container directory."""
        result = self.execute(f"ls -la /workspace/{path}")
        files = []
        for line in result.output.split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 9:
                name = parts[-1]
                if name not in (".", ".."):
                    files.append(
                        {
                            "path": f"{path}/{name}".replace("./", ""),
                            "is_dir": parts[0].startswith("d"),
                            "size": int(parts[4]) if parts[0].startswith("-") else 0,
                        }
                    )
        return files
