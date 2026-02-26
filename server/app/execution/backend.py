"""Execution backend implementations for Cognition.

Provides Docker container execution with kernel-level isolation.
This module has been refactored to use deepagents' SandboxBackendProtocol directly.

Layer: 3 (Execution)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        import subprocess
        import tempfile

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
        import subprocess
        import tempfile

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
