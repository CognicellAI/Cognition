"""Sandbox backend implementations for DeepAgents.

This module provides sandbox backends that combine:
1. Native filesystem operations via deepagents.backends.FilesystemBackend
2. Command execution via LocalShellBackend (local) or DockerExecutionBackend (Docker)
3. Kubernetes sandbox execution via agent-sandbox CRD (Kubernetes)

The local backend is used for development; the Docker backend provides
kernel-level isolation per session for production; the Kubernetes backend
provides K8s-native isolation for production deployments on Kubernetes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from deepagents.backends import FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    ReadResult,
    SandboxBackendProtocol,
)

logger = structlog.get_logger(__name__)


class CognitionLocalSandboxBackend(LocalShellBackend, SandboxBackendProtocol):
    """Local sandbox backend built on Deep Agents' default LocalShellBackend.

    Cognition keeps the protected-path write guard, but local command execution
    intentionally uses Deep Agents' default shell semantics. This preserves the
    behavior that agent prompts and tools already assume for commands that rely
    on shell parsing, pipes, redirects, and shell builtins.
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
        sandbox_env = {
            "GH_TOKEN": os.environ.get("GH_TOKEN", ""),
            "COGNITION_WORKSPACE_ROOT": str(Path(root_dir).resolve()),
            "HOME": os.environ.get("HOME", "/home/cognition"),
            "PATH": os.environ.get("PATH", ""),
        }

        # LocalShellBackend virtual_mode rewrites absolute paths like /workspace/... into
        # <root>/workspace/... which breaks file tools after repo clone. Keep local shell
        # semantics and env control, but disable virtual_mode so file tools and execute()
        # resolve the same concrete paths.
        super().__init__(root_dir=root_dir, virtual_mode=False, env=sandbox_env, inherit_env=False)
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
        self._docker_backend: Any | None = None

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._id

    def _get_docker_backend(self) -> Any:
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

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command inside the Docker container.

        Commands are run via docker exec_run inside an isolated container.
        The workspace directory is volume-mounted so file changes are visible.

        Args:
            command: Shell command to execute.
            timeout: Optional per-command timeout override in seconds.
                Forwarded to the underlying Docker backend.
        """
        docker_backend = self._get_docker_backend()
        result = docker_backend.execute(command, timeout=timeout)

        return ExecuteResponse(
            output=result.output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )


class CognitionKubernetesSandboxBackend(SandboxBackendProtocol):
    """Kubernetes sandbox backend with Cognition policy enforcement.

    Wraps ``langchain_k8s_sandbox.K8sSandbox`` (a ``BaseSandbox`` subclass)
    with Cognition-specific policy:

    - Protected path enforcement (same as CognitionLocalSandboxBackend)
    - User/org/project labels derived from CognitionContext for multi-tenant scoping
    - Session-scoped lifecycle tied to Cognition session creation/destruction

    The K8sSandbox is lazily initialized on first ``execute()`` — no Sandbox CR
    is created until code actually needs to run.
    """

    def __init__(
        self,
        root_dir: str | Path,
        sandbox_id: str | None = None,
        template: str = "cognition-sandbox",
        namespace: str = "default",
        router_url: str = "http://sandbox-router-svc.default.svc.cluster.local:8080",
        labels: dict[str, str] | None = None,
        ttl: int | None = 3600,
        protected_paths: list[str] | None = None,
        warm_pool: str | None = None,
    ):
        """Initialize the Kubernetes sandbox backend.

        Args:
            root_dir: Workspace directory (used for protected path resolution).
            sandbox_id: Unique identifier for this sandbox.
            template: SandboxTemplate CR name for the sandbox pod spec.
            namespace: Kubernetes namespace for sandbox CRs.
            router_url: URL of the sandbox-router service.
            labels: Labels applied to the Sandbox CR (scoping metadata).
            ttl: Time-to-live in seconds for sandbox auto-cleanup.
            protected_paths: List of protected path prefixes.
                Defaults to [".cognition"].
            warm_pool: Optional SandboxWarmPool CR name.
        """
        self._root_dir = Path(root_dir).resolve()
        self._id = sandbox_id or f"cognition-k8s-{id(self)}"
        self._template = template
        self._namespace = namespace
        self._router_url = router_url
        self._labels = labels or {}
        self._ttl = ttl
        self._protected_paths = protected_paths or [".cognition"]
        self._warm_pool = warm_pool

        self._backend: Any | None = None

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._id

    def _get_backend(self) -> Any:
        """Lazily initialize the K8sSandbox backend.

        Returns:
            K8sSandbox instance from langchain-k8s-sandbox.

        Raises:
            RuntimeError: If langchain-k8s-sandbox is not installed.
        """
        if self._backend is None:
            try:
                from langchain_k8s_sandbox import K8sSandbox
            except ImportError as e:
                raise RuntimeError(
                    "langchain-k8s-sandbox is required for the kubernetes sandbox backend. "
                    "Install with: pip install cognition[k8s]"
                ) from e

            self._backend = K8sSandbox(
                template=self._template,
                namespace=self._namespace,
                router_url=self._router_url,
                labels=self._labels,
                ttl=self._ttl,
                warm_pool=self._warm_pool,
            )
            logger.info(
                "K8s sandbox backend initialized",
                sandbox_id=self._id,
                template=self._template,
                namespace=self._namespace,
            )
        return self._backend

    def _is_protected_path(self, path: str) -> bool:
        """Check if a path is protected.

        Args:
            path: The path to check (relative or absolute).

        Returns:
            True if the path is protected, False otherwise.
        """
        try:
            resolved = (self._root_dir / path).resolve()
            resolved_str = str(resolved)
            for protected in self._protected_paths:
                protected_full = (self._root_dir / protected).resolve()
                if resolved_str.startswith(str(protected_full)):
                    return True
        except (ValueError, OSError):
            pass
        return False

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command inside the K8s sandbox pod.

        Args:
            command: Shell command to execute.
            timeout: Optional per-command timeout override in seconds.
        """
        backend = self._get_backend()
        result: ExecuteResponse = backend.execute(command, timeout=timeout)
        return result

    def write(self, file_path: str, content: str) -> Any:
        """Write content to file with protected path check.

        Args:
            file_path: File path to write to.
            content: Content to write.

        Returns:
            WriteResult from the backend.

        Raises:
            PermissionError: If the path is protected.
        """
        if self._is_protected_path(file_path):
            raise PermissionError(f"Writing to protected path is not allowed: {file_path}")
        backend = self._get_backend()
        return backend.write(file_path, content)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Read file content from the sandbox.

        Args:
            file_path: File path to read.
            offset: Line offset to start reading from.
            limit: Maximum number of lines to read.

        Returns:
            File content wrapped in a backend ReadResult.
        """
        backend = self._get_backend()
        result: ReadResult = backend.read(file_path, offset=offset, limit=limit)
        return result

    def ls_info(self, path: str) -> Any:
        """List files and directories in the sandbox.

        Args:
            path: Directory path to list.

        Returns:
            List of FileInfo objects.
        """
        backend = self._get_backend()
        return backend.ls_info(path)

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        """Search for a pattern in sandbox files.

        Args:
            pattern: Regex pattern to search for.
            path: Directory path to search in.
            glob: File glob pattern to filter.

        Returns:
            List of GrepMatch objects or string.
        """
        backend = self._get_backend()
        return backend.grep_raw(pattern, path=path, glob=glob)

    def glob_info(self, pattern: str, path: str = "/") -> Any:
        """Find files matching a glob pattern in the sandbox.

        Args:
            pattern: Glob pattern to match.
            path: Directory path to search in.

        Returns:
            List of FileInfo objects.
        """
        backend = self._get_backend()
        return backend.glob_info(pattern, path=path)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Any:
        """Edit a file in the sandbox with protected path check.

        Args:
            file_path: File path to edit.
            old_string: String to find.
            new_string: Replacement string.
            replace_all: Replace all occurrences.

        Returns:
            EditResult from the backend.

        Raises:
            PermissionError: If the path is protected.
        """
        if self._is_protected_path(file_path):
            raise PermissionError(f"Editing protected path is not allowed: {file_path}")
        backend = self._get_backend()
        return backend.edit(file_path, old_string, new_string, replace_all=replace_all)

    def download_files(self, paths: list[str]) -> list[Any]:
        """Download files from the K8s sandbox pod.

        Delegates to K8sSandbox which uses the sandbox pod HTTP /download API.

        Args:
            paths: List of file paths to download.

        Returns:
            List of FileDownloadResponse objects.
        """
        backend = self._get_backend()
        return backend.download_files(paths)

    def upload_files(self, files: list[Any]) -> list[Any]:
        """Upload files to the K8s sandbox pod.

        Delegates to K8sSandbox which uses the sandbox pod HTTP /upload API.

        Args:
            files: List of FileUploadRequest objects.

        Returns:
            List of FileUploadResponse objects.
        """
        backend = self._get_backend()
        return backend.upload_files(files)

    def terminate(self) -> None:
        """Terminate the K8s sandbox and clean up resources.

        Safe to call multiple times. Subsequent ``execute()`` calls after
        terminate will create a new sandbox.
        """
        if self._backend is not None:
            try:
                self._backend.terminate()
                logger.info("K8s sandbox terminated", sandbox_id=self._id)
            except Exception as e:
                logger.warning("K8s sandbox terminate failed", error=str(e))
            finally:
                self._backend = None


def create_sandbox_backend(
    root_dir: str | Path,
    sandbox_id: str | None = None,
    sandbox_backend: str = "local",
    docker_image: str = "cognition-sandbox:latest",
    docker_network: str = "none",
    docker_memory_limit: str = "512m",
    docker_cpu_limit: float = 1.0,
    docker_host_workspace: str = "",
    k8s_template: str = "cognition-sandbox",
    k8s_namespace: str = "default",
    k8s_router_url: str = "http://sandbox-router-svc.default.svc.cluster.local:8080",
    k8s_ttl: int = 3600,
    k8s_warm_pool: str | None = None,
    labels: dict[str, str] | None = None,
) -> FilesystemBackend | CognitionKubernetesSandboxBackend:
    """Factory for creating sandbox backends from settings.

    Args:
        root_dir: Workspace root directory.
        sandbox_id: Unique identifier for the sandbox.
        sandbox_backend: Backend type - "local", "docker", or "kubernetes".
        docker_image: Docker image for sandbox containers.
        docker_network: Docker network mode.
        docker_memory_limit: Container memory limit.
        docker_cpu_limit: Container CPU limit.
        docker_host_workspace: Host filesystem path for Docker volume mount.
        k8s_template: SandboxTemplate CR name for K8s sandbox pods.
        k8s_namespace: Kubernetes namespace for sandbox CRs.
        k8s_router_url: URL of the sandbox-router service.
        k8s_ttl: Time-to-live in seconds for K8s sandbox auto-cleanup.
        k8s_warm_pool: Optional SandboxWarmPool CR name.
        labels: Labels applied to the Sandbox CR (K8s only).

    Returns:
        A sandbox backend implementing both FilesystemBackend and
        SandboxBackendProtocol (for local/docker), or
        CognitionKubernetesSandboxBackend (for kubernetes).

    Raises:
        ValueError: If sandbox_backend is not "local", "docker", or "kubernetes".
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
    elif sandbox_backend == "kubernetes":
        return CognitionKubernetesSandboxBackend(
            root_dir=root_dir,
            sandbox_id=sandbox_id,
            template=k8s_template,
            namespace=k8s_namespace,
            router_url=k8s_router_url,
            ttl=k8s_ttl,
            labels=labels,
            warm_pool=k8s_warm_pool,
        )
    else:
        raise ValueError(
            f"Unknown sandbox_backend: {sandbox_backend!r}. "
            "Must be 'local', 'docker', or 'kubernetes'."
        )


def validate_k8s_sandbox_config(
    namespace: str = "default",
    router_url: str = "http://sandbox-router-svc.default.svc.cluster.local:8080",
) -> list[str]:
    """Validate K8s sandbox prerequisites at startup.

    Checks that the Sandbox CRD is installed and the router is reachable.
    Returns a list of warning messages for issues that are not fatal.

    Raises:
        RuntimeError: If the Sandbox CRD is not installed (fatal misconfiguration).
    """
    warnings: list[str] = []

    try:
        from kubernetes import client as k8s_client
        from kubernetes.config import load_incluster_config

        try:
            load_incluster_config()
        except Exception:
            from kubernetes.config import load_kube_config

            try:
                load_kube_config()
            except Exception:
                warnings.append("No K8s config available (not in cluster, no kubeconfig)")
                return warnings

        api = k8s_client.ApiextensionsV1Api()
        try:
            api.read_custom_resource_definition(name="sandboxes.agents.x-k8s.io")
        except k8s_client.rest.ApiException as e:
            if e.status == 404:
                raise RuntimeError(
                    "Sandbox CRD 'sandboxes.agents.x-k8s.io' not found. "
                    "Install the agent-sandbox controller: "
                    "kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.3.10/manifest.yaml"
                ) from e
            warnings.append(f"Could not verify Sandbox CRD: {e}")

        try:
            api.read_custom_resource_definition(name="sandboxclaims.extensions.agents.x-k8s.io")
        except k8s_client.rest.ApiException as e:
            if e.status == 404:
                raise RuntimeError(
                    "SandboxClaim CRD 'sandboxclaims.extensions.agents.x-k8s.io' not found. "
                    "Install agent-sandbox extensions: "
                    "kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.3.10/extensions.yaml"
                ) from e
            warnings.append(f"Could not verify SandboxClaim CRD: {e}")

    except ImportError:
        warnings.append("kubernetes Python package not installed; skipping CRD validation")

    import httpx

    try:
        resp = httpx.get(f"{router_url}/healthz", timeout=5)
        if resp.status_code != 200:
            warnings.append(
                f"Router health check returned {resp.status_code}: {router_url}/healthz"
            )
    except Exception as e:
        warnings.append(f"Router not reachable at {router_url}: {e}")

    if warnings:
        logger.warning("K8s sandbox validation warnings", warnings=warnings)
    else:
        logger.info("K8s sandbox validation passed")

    return warnings
