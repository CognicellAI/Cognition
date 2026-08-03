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

import base64
import hashlib
import os
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, cast

import structlog
from deepagents.backends import FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

logger = structlog.get_logger(__name__)

from server.app.storage.config_models import LambdaMicroVmQuota, SandboxProfile  # noqa: E402


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

    All model-directed filesystem and command operations are routed through the
    per-session container. The inherited FilesystemBackend supplies the protocol
    shape only; its host filesystem methods are fully overridden here.

    This provides:
    - File I/O through Docker archive and exec APIs
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
        self._protected_paths = {".cognition"}

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

    @staticmethod
    def _relative_path(path: str | None) -> str:
        """Normalize a virtual sandbox path and reject traversal."""
        raw = path or "."
        if "\x00" in raw or "\\" in raw:
            raise ValueError("Invalid sandbox path")
        if raw == "/workspace":
            raw = "."
        elif raw.startswith("/workspace/"):
            raw = raw.removeprefix("/workspace/")
        else:
            raw = raw.lstrip("/")
        candidate = PurePosixPath(raw or ".")
        if ".." in candidate.parts:
            raise ValueError("Path traversal is not allowed")
        normalized = candidate.as_posix()
        return "." if normalized in {"", "/"} else normalized

    def _is_protected_path(self, path: str) -> bool:
        relative = self._relative_path(path)
        return any(
            relative == protected or relative.startswith(f"{protected}/")
            for protected in self._protected_paths
        )

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read a file only through the assigned Docker sandbox."""
        try:
            relative = self._relative_path(file_path)
            content = self._get_docker_backend().read_file_bytes(relative)
            try:
                text = content.decode("utf-8")
                lines = text.splitlines(keepends=True)
                if lines and offset >= len(lines):
                    return ReadResult(
                        error=(
                            f"Line offset {offset} exceeds file length "
                            f"({len(lines)} lines)"
                        )
                    )
                selected = "".join(lines[offset : offset + limit])
                return ReadResult(
                    file_data={"content": selected, "encoding": "utf-8"}
                )
            except UnicodeDecodeError:
                return ReadResult(
                    file_data={
                        "content": base64.standard_b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    }
                )
        except Exception as exc:
            return ReadResult(error=f"Error reading file '{file_path}': {exc}")

    def write(self, file_path: str, content: str) -> WriteResult:
        """Create a file only through the assigned Docker sandbox."""
        try:
            relative = self._relative_path(file_path)
            if self._is_protected_path(relative):
                raise PermissionError(
                    f"Writing to protected path is not allowed: {file_path}"
                )
            docker_backend = self._get_docker_backend()
            if docker_backend.path_exists(relative):
                return WriteResult(
                    error=(
                        f"Cannot write to {file_path} because it already exists. "
                        "Read and then make an edit, or write to a new path."
                    )
                )
            docker_backend.write_file(relative, content)
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=f"Error writing file '{file_path}': {exc}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Edit a UTF-8 file only through the assigned Docker sandbox."""
        try:
            relative = self._relative_path(file_path)
            if self._is_protected_path(relative):
                raise PermissionError(
                    f"Editing protected path is not allowed: {file_path}"
                )
            docker_backend = self._get_docker_backend()
            content = docker_backend.read_file(relative)
            occurrences = content.count(old_string)
            if occurrences == 0:
                return EditResult(error=f"String not found in {file_path}")
            if occurrences > 1 and not replace_all:
                return EditResult(
                    error=(
                        f"String occurs {occurrences} times in {file_path}; "
                        "set replace_all=true or provide a unique string"
                    )
                )
            updated = (
                content.replace(old_string, new_string)
                if replace_all
                else content.replace(old_string, new_string, 1)
            )
            docker_backend.write_file(relative, updated)
            return EditResult(
                path=file_path,
                occurrences=occurrences if replace_all else 1,
            )
        except Exception as exc:
            return EditResult(error=f"Error editing file '{file_path}': {exc}")

    def ls(self, path: str) -> LsResult:
        """List a directory only through the assigned Docker sandbox."""
        try:
            entries = self._get_docker_backend().list_files(
                self._relative_path(path)
            )
            return LsResult(entries=cast(list[Any], entries))
        except Exception as exc:
            return LsResult(error=f"Cannot list '{path}': {exc}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        """Search text only through the assigned Docker sandbox."""
        del context_lines  # Docker's adapter does not expose surrounding-line search.
        try:
            matches = self._get_docker_backend().grep_files(
                pattern,
                self._relative_path(path),
                glob,
            )
            if max_count is not None:
                matches = matches[:max_count]
            return GrepResult(matches=cast(list[Any], matches))
        except Exception as exc:
            return GrepResult(error=f"Error searching sandbox: {exc}", matches=[])

    def glob(self, pattern: str, path: str | None = "/") -> GlobResult:
        """Discover files only through the assigned Docker sandbox."""
        try:
            if pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
                raise ValueError("Invalid glob pattern")
            matches = self._get_docker_backend().glob_files(
                pattern,
                self._relative_path(path),
            )
            return GlobResult(matches=cast(list[Any], matches))
        except Exception as exc:
            return GlobResult(error=f"Error globbing sandbox: {exc}", matches=[])

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files through the assigned Docker sandbox."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                content = self._get_docker_backend().read_file_bytes(
                    self._relative_path(path)
                )
                responses.append(
                    FileDownloadResponse(path=path, content=content, error=None)
                )
            except FileNotFoundError:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error="file_not_found",
                    )
                )
            except IsADirectoryError:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error="is_directory",
                    )
                )
            except (PermissionError, ValueError):
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error="invalid_path",
                    )
                )
        return responses

    def upload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        """Upload files through the assigned Docker sandbox."""
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                relative = self._relative_path(path)
                if self._is_protected_path(relative):
                    raise PermissionError(path)
                self._get_docker_backend().write_file_bytes(relative, content)
                responses.append(FileUploadResponse(path=path, error=None))
            except PermissionError:
                responses.append(
                    FileUploadResponse(path=path, error="permission_denied")
                )
            except ValueError:
                responses.append(
                    FileUploadResponse(path=path, error="invalid_path")
                )
            except IsADirectoryError:
                responses.append(
                    FileUploadResponse(path=path, error="is_directory")
                )
        return responses

    def terminate(self) -> None:
        """Destroy the assigned Docker sandbox."""
        if self._docker_backend is not None:
            self._docker_backend.terminate()
            self._docker_backend = None


def _arn_region(arn: str) -> str | None:
    parts = arn.split(":")
    if len(parts) > 3:
        return parts[3] or None
    return None


def _arn_partition(arn: str) -> str:
    parts = arn.split(":")
    if len(parts) > 1 and parts[1]:
        return parts[1]
    return "aws"


def _aws_managed_network_connector_arn(
    *,
    image_arn: str,
    region: str | None,
    connector_name: str,
) -> str | None:
    resolved_region = region or _arn_region(image_arn)
    if not resolved_region:
        return None
    partition = _arn_partition(image_arn)
    return (
        f"arn:{partition}:lambda:{resolved_region}:aws:"
        f"network-connector:aws-network-connector:{connector_name}"
    )


def _role_fingerprint(role_arn: str | None) -> str | None:
    if not role_arn:
        return None
    return hashlib.sha256(role_arn.encode("utf-8")).hexdigest()[:16]


def _lambda_microvm_logging_mode(profile: SandboxProfile) -> str | None:
    if profile.logging is not None:
        if profile.logging.disabled is not None:
            return "disabled"
        if profile.logging.cloud_watch is not None:
            return "cloud_watch"
    legacy_logging = profile.extra.get("logging")
    if isinstance(legacy_logging, dict):
        if "disabled" in legacy_logging:
            return "disabled"
        if "cloudWatch" in legacy_logging or "cloud_watch" in legacy_logging:
            return "cloud_watch"
        return "custom"
    return None


class CognitionAwsLambdaMicroVmSandboxBackend(SandboxBackendProtocol):
    """AWS Lambda MicroVM sandbox backend wrapper for Cognition.

    Cognition resolves trusted builder config (``SandboxProfile`` plus optional
    agent execution role) and delegates the Deep Agents backend contract to the
    reusable ``langchain-aws-lambda-microvms`` package.
    """

    def __init__(
        self,
        root_dir: str | Path,
        sandbox_id: str | None = None,
        profile: str = "default",
        execution_role_arn: str | None = None,
        profile_config: SandboxProfile | None = None,
        protected_paths: list[str] | None = None,
    ) -> None:
        self._root_dir = Path(root_dir).resolve()
        self._id = sandbox_id or f"cognition-aws-lambda-microvm-{id(self)}"
        self._profile = profile
        self._execution_role_arn = execution_role_arn
        self._profile_config = profile_config
        self._protected_paths = protected_paths or [".cognition"]
        self._backend: Any | None = None
        self._last_runtime_metadata: dict[str, Any] = {}

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        if self._backend is not None:
            return cast(str, self._backend.id)
        return self._id

    @property
    def profile(self) -> str:
        """Return the trusted sandbox profile selected for this backend."""
        return self._profile

    @property
    def execution_role_arn(self) -> str | None:
        """Return the trusted sandbox execution role ARN, if configured."""
        if self._execution_role_arn:
            return self._execution_role_arn
        if self._profile_config is not None:
            return self._profile_config.default_execution_role_arn
        return None

    @property
    def quota(self) -> LambdaMicroVmQuota | None:
        """Return the Cognition-side quota policy for this profile."""
        if self._profile_config is None:
            return None
        return self._profile_config.quota

    @property
    def runtime_metadata(self) -> dict[str, Any]:
        """Return token-free MicroVM metadata for observability/persistence."""
        metadata: dict[str, Any] = {
            "backend": "aws_lambda_microvm",
            "profile": self._profile,
        }
        if self._profile_config is not None:
            metadata.update(self._profile_runtime_metadata(self._profile_config))
        if self._backend is not None and hasattr(self._backend, "runtime_metadata"):
            self._last_runtime_metadata = cast(dict[str, Any], self._backend.runtime_metadata)
            metadata.update(self._last_runtime_metadata)
        elif self._last_runtime_metadata:
            metadata.update(self._last_runtime_metadata)
        return metadata

    def _profile_runtime_metadata(self, profile: SandboxProfile) -> dict[str, Any]:
        """Return safe profile metadata useful for lifecycle correlation."""
        return {
            "microvm_id": None,
            "endpoint": None,
            "image": profile.image_arn,
            "image_version": profile.image_version,
            "status": None,
            "aws_state": None,
            "execution_role_fingerprint": _role_fingerprint(self.execution_role_arn),
            "region": profile.region,
            "port": profile.port,
            "maximum_duration_seconds": profile.maximum_duration_seconds,
            "token_expiration_minutes": profile.token_expiration_minutes,
            "egress_mode": profile.egress_mode,
            "ingress_network_connector_count": len(profile.ingress_network_connector_arns),
            "egress_network_connector_count": len(profile.egress_network_connector_arns),
            "idle_policy": profile.idle_policy.model_dump(mode="json")
            if profile.idle_policy is not None
            else None,
            "logging_mode": _lambda_microvm_logging_mode(profile),
            "quota": profile.quota.model_dump(exclude_none=True, mode="json")
            if profile.quota is not None
            else None,
            "run_hook_configured": bool(
                profile.run_hook_payload or profile.extra.get("run_hook_payload")
            ),
        }

    def _is_protected_path(self, path: str) -> bool:
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

    def _ingress_connectors(self, profile: SandboxProfile) -> list[str]:
        if profile.ingress_network_connector_arns:
            return list(profile.ingress_network_connector_arns)
        default = _aws_managed_network_connector_arn(
            image_arn=profile.image_arn,
            region=profile.region,
            connector_name="ALL_INGRESS",
        )
        return [default] if default else []

    def _egress_connectors(self, profile: SandboxProfile) -> list[str]:
        if profile.egress_network_connector_arns:
            return list(profile.egress_network_connector_arns)
        if profile.egress_mode == "internet":
            default = _aws_managed_network_connector_arn(
                image_arn=profile.image_arn,
                region=profile.region,
                connector_name="INTERNET_EGRESS",
            )
            return [default] if default else []
        return []

    def _get_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        if self._profile_config is None:
            raise RuntimeError(
                f"SandboxProfile {self._profile!r} was not resolved for the "
                "aws_lambda_microvm backend. Register the profile in ConfigStore "
                "or seed it from .cognition/config.yaml before selecting this backend."
            )

        try:
            from langchain_aws_lambda_microvms import LambdaMicroVmSandbox
        except ImportError as e:
            raise RuntimeError(
                "langchain-aws-lambda-microvms is required for the "
                "aws_lambda_microvm sandbox backend."
            ) from e

        profile = self._profile_config
        idle_policy = (
            {
                "maxIdleDurationSeconds": profile.idle_policy.max_idle_duration_seconds,
                "suspendedDurationSeconds": profile.idle_policy.suspended_duration_seconds,
                "autoResumeEnabled": profile.idle_policy.auto_resume_enabled,
            }
            if profile.idle_policy is not None
            else None
        )
        logging_config = (
            profile.logging.to_aws_request()
            if profile.logging is not None
            else cast(dict[str, Any] | None, profile.extra.get("logging"))
        )
        run_hook_payload = profile.run_hook_payload or cast(
            str | None,
            profile.extra.get("run_hook_payload"),
        )
        workspace_root = str(profile.extra.get("workspace_root", "/workspace"))
        launch_timeout_seconds = int(profile.extra.get("launch_timeout_seconds", 120))
        healthcheck_timeout_seconds = int(profile.extra.get("healthcheck_timeout_seconds", 60))

        self._backend = LambdaMicroVmSandbox(
            image_identifier=profile.image_arn,
            image_version=profile.image_version,
            region_name=profile.region,
            execution_role_arn=self.execution_role_arn,
            ingress_network_connector_arns=self._ingress_connectors(profile),
            egress_network_connector_arns=self._egress_connectors(profile),
            idle_policy=idle_policy,
            logging_config=logging_config,
            run_hook_payload=run_hook_payload,
            maximum_duration_seconds=profile.maximum_duration_seconds,
            port=profile.port,
            token_expiration_minutes=profile.token_expiration_minutes,
            workspace_root=workspace_root,
            sandbox_id=self._id,
            launch_timeout_seconds=launch_timeout_seconds,
            healthcheck_timeout_seconds=healthcheck_timeout_seconds,
        )
        logger.info(
            "AWS Lambda MicroVM sandbox backend initialized",
            sandbox_id=self._id,
            profile=self._profile,
            image=profile.image_arn,
            image_version=profile.image_version,
            region=profile.region,
        )
        return self._backend

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command in the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        result: ExecuteResponse = backend.execute(command, timeout=timeout)
        return result

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read file content from the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        result: ReadResult = backend.read(file_path, offset=offset, limit=limit)
        return result

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write file content to the AWS Lambda MicroVM sandbox."""
        if self._is_protected_path(file_path):
            raise PermissionError(f"Writing to protected path is not allowed: {file_path}")
        backend = self._get_backend()
        result: WriteResult = backend.write(file_path, content)
        return result

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        """Edit file content in the AWS Lambda MicroVM sandbox."""
        if self._is_protected_path(file_path):
            raise PermissionError(f"Editing protected path is not allowed: {file_path}")
        backend = self._get_backend()
        return backend.edit(file_path, old_string, new_string, replace_all=replace_all)

    def ls(self, path: str) -> LsResult:
        """List directory entries in the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        result: LsResult = backend.ls(path)
        return result

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search files in the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        kwargs: dict[str, Any] = {"path": path, "glob": glob}
        if max_count is not None:
            kwargs["max_count"] = max_count
        result: GrepResult = backend.grep(pattern, **kwargs)
        return result

    def glob(self, pattern: str, path: str | None = "/") -> GlobResult:
        """Find matching files in the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        result: GlobResult = backend.glob(pattern, path=path)
        return result

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        return cast(list[FileDownloadResponse], backend.download_files(paths))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the AWS Lambda MicroVM sandbox."""
        backend = self._get_backend()
        return cast(list[FileUploadResponse], backend.upload_files(files))

    def terminate(self) -> None:
        """Terminate the AWS Lambda MicroVM sandbox if one was created."""
        if self._backend is not None:
            try:
                self._backend.terminate()
                if hasattr(self._backend, "runtime_metadata"):
                    self._last_runtime_metadata = cast(
                        dict[str, Any], self._backend.runtime_metadata
                    )
                log_args = {
                    "sandbox_id": self._id,
                    "profile": self._profile,
                    "microvm_id": self._last_runtime_metadata.get("microvm_id"),
                    "image": self._last_runtime_metadata.get("image"),
                    "image_version": self._last_runtime_metadata.get("image_version"),
                    "region": self._last_runtime_metadata.get("region"),
                    "aws_state": self._last_runtime_metadata.get("aws_state"),
                    "teardown_status": self._last_runtime_metadata.get("teardown_status"),
                    "teardown_attempt": self._last_runtime_metadata.get("teardown_attempt"),
                    "execution_role_fingerprint": self._last_runtime_metadata.get(
                        "execution_role_fingerprint"
                    ),
                }
                if log_args["teardown_status"] in {"pending", "failed"}:
                    logger.warning("AWS Lambda MicroVM sandbox teardown incomplete", **log_args)
                else:
                    logger.info("AWS Lambda MicroVM sandbox teardown finished", **log_args)
            except Exception as e:
                if hasattr(self._backend, "runtime_metadata"):
                    self._last_runtime_metadata = cast(
                        dict[str, Any], self._backend.runtime_metadata
                    )
                self._last_runtime_metadata.update(
                    {
                        "teardown_status": "failed",
                        "teardown_error_code": e.__class__.__name__,
                        "teardown_error_message": str(e),
                    }
                )
                logger.warning("AWS Lambda MicroVM sandbox terminate failed", error=str(e))
            finally:
                self._backend = None


class CognitionKubernetesSandboxBackend(SandboxBackendProtocol):
    """Kubernetes sandbox backend with Cognition policy enforcement.

    Wraps ``langchain_k8s_sandbox.K8sSandbox`` (a ``BaseSandbox`` subclass)
    with Cognition-specific policy:

    - Protected path enforcement (same as CognitionLocalSandboxBackend)
    - Builder-defined labels derived from effective_scope for runtime isolation
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

            # Verify runtime readiness before agent execution
            self._verify_sandbox_runtime()

        return self._backend

    def _verify_sandbox_runtime(self) -> None:
        """Verify sandbox runtime readiness before agent work begins.

        Checks env vars, workspace root, writable directories, and optional
        GitHub auth status. Logs warnings for any failures so builders can
        diagnose issues before an agent run fails mysteriously.
        """
        if self._backend is None:
            return

        checks: list[tuple[str, str, bool | None]] = []

        # Check workspace root
        expected_root = str(self._root_dir)
        result = self._backend.execute(
            f'test -d {shlex.quote(expected_root)} && echo "ok" || echo "missing"'
        )
        workspace_ok = result.exit_code == 0
        checks.append(("workspace_root", f"Expected {expected_root}", workspace_ok))

        # Check writable workspace
        result = self._backend.execute(
            f'test -w {shlex.quote(expected_root)} && echo "ok" || echo "ro"'
        )
        writable_ok = result.exit_code == 0
        checks.append(("workspace_writable", expected_root, writable_ok))

        # Check writable temp
        result = self._backend.execute('test -w /tmp && echo "ok" || echo "ro"')
        tmp_ok = result.exit_code == 0
        checks.append(("temp_writable", "/tmp", tmp_ok))

        # Check env vars
        result = self._backend.execute("env | grep -c COGNITION_WORKSPACE_ROOT")
        env_ok = (
            result.exit_code == 0
            and result.output.strip().isdigit()
            and int(result.output.strip()) > 0
        )
        checks.append(("env_var", "COGNITION_WORKSPACE_ROOT", env_ok))

        # Check GitHub auth if token is expected
        gh_token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
        if gh_token:
            result = self._backend.execute("gh auth status 2>&1 || echo 'not_authenticated'")
            gh_ok = "not_authenticated" not in result.output
            checks.append(("github_auth", "gh auth status", gh_ok))

        failures = [(name, detail) for name, detail, ok in checks if ok is not None and not ok]
        if failures:
            logger.warning(
                "Sandbox runtime verification found issues",
                sandbox_id=self._id,
                failures=dict(failures),
            )
        else:
            logger.info(
                "Sandbox runtime verification passed",
                sandbox_id=self._id,
                checks_count=len(checks),
            )

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

    def ls(self, path: str) -> LsResult:
        """List directory entries using Deep Agents' current result API."""
        backend = self._get_backend()
        result: LsResult = backend.ls(path)
        return result

    def ls_info(self, path: str) -> Any:
        """List files and directories in the sandbox.

        Args:
            path: Directory path to list.

        Returns:
            List of FileInfo objects.
        """
        result = self.ls(path)
        if result.error is not None:
            raise NotImplementedError(result.error)
        return result.entries or []

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search files using Deep Agents' current result API."""
        backend = self._get_backend()
        kwargs: dict[str, Any] = {"path": path, "glob": glob}
        if max_count is not None:
            kwargs["max_count"] = max_count
        result: GrepResult = backend.grep(pattern, **kwargs)
        return result

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> Any:
        """Search for a pattern in sandbox files.

        Args:
            pattern: Regex pattern to search for.
            path: Directory path to search in.
            glob: File glob pattern to filter.

        Returns:
            List of GrepMatch objects or string.
        """
        result = self.grep(pattern, path=path, glob=glob)
        if result.error is not None:
            return result.error
        return result.matches or []

    def glob(self, pattern: str, path: str | None = "/") -> GlobResult:
        """Find matching files using Deep Agents' current result API."""
        backend = self._get_backend()
        result: GlobResult = backend.glob(pattern, path=path)
        return result

    def glob_info(self, pattern: str, path: str = "/") -> Any:
        """Find files matching a glob pattern in the sandbox.

        Args:
            pattern: Glob pattern to match.
            path: Directory path to search in.

        Returns:
            List of FileInfo objects.
        """
        result = self.glob(pattern, path=path)
        if result.error is not None:
            raise NotImplementedError(result.error)
        return result.matches or []

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
        return cast(list[Any], backend.download_files(paths))

    def upload_files(self, files: list[Any]) -> list[Any]:
        """Upload files to the K8s sandbox pod.

        Delegates to K8sSandbox which uses the sandbox pod HTTP /upload API.

        Args:
            files: List of FileUploadRequest objects.

        Returns:
            List of FileUploadResponse objects.
        """
        backend = self._get_backend()
        return cast(list[Any], backend.upload_files(files))

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
    aws_lambda_microvm_profile: str = "default",
    aws_lambda_microvm_execution_role_arn: str | None = None,
    aws_lambda_microvm_profile_config: SandboxProfile | None = None,
) -> (
    FilesystemBackend | CognitionKubernetesSandboxBackend | CognitionAwsLambdaMicroVmSandboxBackend
):
    """Factory for creating sandbox backends from settings.

    Args:
        root_dir: Workspace root directory.
        sandbox_id: Unique identifier for the sandbox.
        sandbox_backend: Backend type - "local", "docker", "kubernetes",
            or "aws_lambda_microvm".
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
        aws_lambda_microvm_profile: Trusted SandboxProfile name for the AWS
            Lambda MicroVM backend.
        aws_lambda_microvm_execution_role_arn: Trusted IAM role ARN assigned to
            the AWS Lambda MicroVM sandbox runtime.
        aws_lambda_microvm_profile_config: Trusted, already-resolved
            SandboxProfile selected from ConfigStore.

    Returns:
        A sandbox backend implementing both FilesystemBackend and
        SandboxBackendProtocol (for local/docker), or
        CognitionKubernetesSandboxBackend (for kubernetes).

    Raises:
        ValueError: If sandbox_backend is unknown.
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
    elif sandbox_backend == "aws_lambda_microvm":
        return CognitionAwsLambdaMicroVmSandboxBackend(
            root_dir=root_dir,
            sandbox_id=sandbox_id,
            profile=aws_lambda_microvm_profile,
            execution_role_arn=aws_lambda_microvm_execution_role_arn,
            profile_config=aws_lambda_microvm_profile_config,
        )
    else:
        raise ValueError(
            f"Unknown sandbox_backend: {sandbox_backend!r}. "
            "Must be 'local', 'docker', 'kubernetes', or 'aws_lambda_microvm'."
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
