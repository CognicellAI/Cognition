"""Execution backend implementations for Cognition.

Provides Docker container execution with kernel-level isolation.
This module has been refactored to use deepagents' SandboxBackendProtocol directly.

Layer: 3 (Execution)
"""

from __future__ import annotations

import io
import json
import tarfile
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
        workspace_root: str = "/workspace",
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
        self.workspace_root = workspace_root.rstrip("/") or "/"
        self._container: Any = None

    def _ensure_container(self) -> None:
        """Ensure container is running."""

        import docker

        if self._container is None:
            client = docker.from_env()  # type: ignore[attr-defined]
            container_name = f"cognition-{self.sandbox_id}"

            # Check if container already exists
            try:
                existing = client.containers.get(container_name)
                if existing.status == "running":
                    self._container = existing
                    return
                else:
                    existing.remove(force=True)
            except docker.errors.NotFound:  # type: ignore[attr-defined]
                pass  # Expected: no existing container, proceed to create

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
                volumes={self.host_workspace: {"bind": self.workspace_root, "mode": "rw"}},
                working_dir=self.workspace_root,
                environment={"COGNITION_WORKSPACE_ROOT": self.workspace_root},
                stdin_open=True,
                tty=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                read_only=True,
                tmpfs={"/tmp": "size=64m", "/home": "size=16m"},
                labels={"cognition.sandbox.id": self.sandbox_id, "cognition.managed": "true"},
            )

    def execute(self, command: str, timeout: float | None = 300.0) -> ExecutionResult:
        """Execute command in Docker container."""
        return self._execute_command(["sh", "-c", command], timeout=timeout)

    def execute_argv(
        self,
        command: list[str],
        timeout: float | None = 300.0,
    ) -> ExecutionResult:
        """Execute a fixed argv without invoking a shell."""
        return self._execute_command(command, timeout=timeout)

    def _execute_command(
        self,
        command: list[str],
        *,
        timeout: float | None,
    ) -> ExecutionResult:
        """Execute an argv in the container and normalize its response."""
        import structlog

        logger = structlog.get_logger(__name__)

        self._ensure_container()
        try:
            exit_code, output = self._container.exec_run(
                cmd=command,
                workdir=self.workspace_root,
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
        """Read a UTF-8 file through the container archive API."""
        return self.read_file_bytes(path).decode("utf-8")

    def read_file_bytes(self, path: str) -> bytes:
        """Read bytes from the sandbox without touching the host filesystem."""
        self._ensure_container()
        stream, _stat = self._container.get_archive(f"{self.workspace_root}/{path}")
        archive = io.BytesIO(b"".join(stream))
        with tarfile.open(fileobj=archive, mode="r:*") as tar:
            members = tar.getmembers()
            if len(members) != 1 or not members[0].isfile():
                raise IsADirectoryError(path)
            extracted = tar.extractfile(members[0])
            if extracted is None:
                raise FileNotFoundError(path)
            return extracted.read()

    def write_file(self, path: str, content: str) -> None:
        """Write a UTF-8 file through the container archive API."""
        self.write_file_bytes(path, content.encode("utf-8"))

    def write_file_bytes(self, path: str, content: bytes) -> None:
        """Write bytes into the sandbox without a host temporary file."""
        self._ensure_container()
        relative = Path(path)
        parent = relative.parent.as_posix()
        destination = self.workspace_root if parent == "." else f"{self.workspace_root}/{parent}"
        mkdir_result = self.execute_argv(["mkdir", "-p", destination])
        if mkdir_result.exit_code != 0:
            raise OSError(mkdir_result.output)

        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo(relative.name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
        archive.seek(0)
        if not self._container.put_archive(destination, archive.read()):
            raise OSError(f"Container rejected file upload: {path}")

    def list_files(self, path: str = ".") -> list[dict]:
        """List files by running a fixed inspection script in the sandbox."""
        script = """
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[2])
target = (root / sys.argv[1]).resolve()
if root != target and root not in target.parents:
    raise SystemExit(2)
if not target.exists():
    raise SystemExit(3)
if not target.is_dir():
    raise SystemExit(4)
rows = []
for child in target.iterdir():
    resolved = child.resolve()
    if root != resolved and root not in resolved.parents:
        continue
    relative = child.relative_to(root).as_posix()
    rows.append({
        "path": "/" + relative + ("/" if child.is_dir() else ""),
        "is_dir": child.is_dir(),
        "size": child.stat().st_size if child.is_file() else 0,
    })
print(json.dumps(sorted(rows, key=lambda row: row["path"])))
"""
        result = self.execute_argv(["python", "-c", script, path, self.workspace_root])
        if result.exit_code == 3:
            raise FileNotFoundError(path)
        if result.exit_code == 4:
            raise NotADirectoryError(path)
        if result.exit_code != 0:
            raise OSError(result.output)
        value = json.loads(result.output)
        if not isinstance(value, list):
            raise OSError("Invalid sandbox listing response")
        return value

    def path_exists(self, path: str) -> bool:
        """Return whether a sandbox path exists without host path inspection."""
        script = """
import pathlib
import sys

root = pathlib.Path(sys.argv[2])
target = (root / sys.argv[1]).resolve()
if root != target and root not in target.parents:
    raise SystemExit(2)
raise SystemExit(0 if target.exists() else 1)
"""
        return self.execute_argv(["python", "-c", script, path, self.workspace_root]).exit_code == 0

    def glob_files(self, pattern: str, path: str = ".") -> list[dict[str, Any]]:
        """Run glob discovery inside the sandbox."""
        script = """
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[3])
base = (root / sys.argv[2]).resolve()
if root != base and root not in base.parents:
    raise SystemExit(2)
rows = []
if base.is_dir():
    for child in base.glob(sys.argv[1]):
        resolved = child.resolve()
        if root != resolved and root not in resolved.parents:
            continue
        relative = child.relative_to(root).as_posix()
        rows.append({
            "path": "/" + relative + ("/" if child.is_dir() else ""),
            "is_dir": child.is_dir(),
            "size": child.stat().st_size if child.is_file() else 0,
        })
print(json.dumps(sorted(rows, key=lambda row: row["path"])))
"""
        result = self.execute_argv(
            ["python", "-c", script, pattern, path, self.workspace_root]
        )
        if result.exit_code != 0:
            raise OSError(result.output)
        value = json.loads(result.output)
        if not isinstance(value, list):
            raise OSError("Invalid sandbox glob response")
        return value

    def grep_files(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run bounded literal text search inside the sandbox."""
        script = """
import fnmatch
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[4])
base = (root / sys.argv[2]).resolve()
if root != base and root not in base.parents:
    raise SystemExit(2)
file_glob = sys.argv[3] or None
files = [base] if base.is_file() else base.rglob("*") if base.is_dir() else []
rows = []
for child in files:
    if not child.is_file() or (file_glob and not fnmatch.fnmatch(child.name, file_glob)):
        continue
    resolved = child.resolve()
    if root != resolved and root not in resolved.parents:
        continue
    try:
        text = child.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    for number, line in enumerate(text.splitlines(), start=1):
        if sys.argv[1] in line:
            rows.append({
                "path": "/" + child.relative_to(root).as_posix(),
                "line": number,
                "text": line,
            })
            if len(rows) >= 1000:
                break
    if len(rows) >= 1000:
        break
print(json.dumps(rows))
"""
        result = self.execute_argv(
            ["python", "-c", script, pattern, path, glob or "", self.workspace_root]
        )
        if result.exit_code != 0:
            raise OSError(result.output)
        value = json.loads(result.output)
        if not isinstance(value, list):
            raise OSError("Invalid sandbox grep response")
        return value

    def terminate(self) -> None:
        """Stop and remove the per-session container deterministically."""
        if self._container is None:
            return
        container = self._container
        self._container = None
        try:
            container.remove(force=True)
        except Exception:
            # Surface teardown failures to the lifecycle manager.
            self._container = container
            raise
