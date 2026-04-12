"""Kubernetes sandbox backend using kubernetes-sigs/agent-sandbox.

This module provides ``K8sSandbox``, a :class:`deepagents.backends.sandbox.BaseSandbox`
subclass that runs commands in Kubernetes Sandbox CRs managed by the agent-sandbox
controller. It uses the ``k8s-agent-sandbox`` Python SDK for sandbox lifecycle and
command execution.

Design decisions:
- Lazy initialization: Sandbox CR is created on first ``execute()``, not in ``__init__``.
  This avoids paying for sandboxes in sessions that never execute code.
- Labels: Generic dict passed through to the Sandbox CR. Callers (Cognition) populate
  these with user/org/project/session IDs for multi-tenant scoping.
- TTL: Applied via the Sandbox CR's ``spec.shutdownTime`` field after creation,
  since the SDK's ``create_sandbox()`` does not expose a TTL parameter.
- File operations: Inherited from ``BaseSandbox`` — all file ops pipe shell commands
  through ``execute()``. Native SDK file transfer is a future optimization.
- Sync only (v1): ``aexecute()`` is provided by ``SandboxBackendProtocol`` via
  ``asyncio.to_thread()`` wrapping this sync ``execute()``.
"""

from __future__ import annotations

import shlex
from datetime import UTC
from typing import Any

import structlog
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

logger = structlog.get_logger(__name__)


class K8sSandbox(BaseSandbox):
    """deepagents sandbox backend using kubernetes-sigs/agent-sandbox.

    Each instance creates a Sandbox CR on first ``execute()`` and routes all
    commands through the agent-sandbox router to an isolated sandbox pod.

    Args:
        template: SandboxTemplate CR name that defines the sandbox pod spec.
        namespace: Kubernetes namespace for sandbox CRs.
        router_url: URL of the sandbox-router service (DirectConnection mode).
        labels: Labels applied to the Sandbox CR (e.g. scoping metadata).
        ttl: Time-to-live in seconds. Maps to ``shutdown_after_seconds``.
            Prevents resource leaks from abandoned sessions.
        server_port: Port the sandbox runtime listens on inside the pod.
        warm_pool: Optional SandboxWarmPool CR name for pre-warmed allocation.
    """

    def __init__(
        self,
        template: str = "cognition-sandbox",
        namespace: str = "default",
        router_url: str = "http://sandbox-router-svc.default.svc.cluster.local:8080",
        labels: dict[str, str] | None = None,
        ttl: int | None = None,
        server_port: int = 8888,
        warm_pool: str | None = None,
    ) -> None:
        self._template = template
        self._namespace = namespace
        self._router_url = router_url
        self._labels = labels or {}
        self._ttl = ttl
        self._server_port = server_port
        self._warm_pool = warm_pool

        self._sandbox_id = f"k8s-{id(self):x}"
        self._sandbox: Any | None = None
        self._client: Any | None = None

    @property
    def id(self) -> str:
        """Return the unique identifier for this sandbox."""
        return self._sandbox_id

    def _ensure_sandbox(self) -> Any:
        """Lazily create the Sandbox CR on first use.

        Returns:
            The agent-sandbox SDK Sandbox object.

        Raises:
            RuntimeError: If the k8s-agent-sandbox SDK is not installed
                or sandbox creation fails.
        """
        if self._sandbox is not None:
            return self._sandbox

        try:
            from k8s_agent_sandbox import SandboxClient
            from k8s_agent_sandbox.models import SandboxDirectConnectionConfig
        except ImportError as e:
            raise RuntimeError(
                "k8s-agent-sandbox is required for K8sSandbox. "
                "Install with: pip install langchain-k8s-sandbox[k8s]"
            ) from e

        connection_config = SandboxDirectConnectionConfig(
            api_url=self._router_url,
            server_port=self._server_port,
        )
        self._client = SandboxClient(connection_config=connection_config)

        create_kwargs: dict[str, Any] = {
            "template": self._template,
            "namespace": self._namespace,
            "labels": self._labels,
        }

        logger.info(
            "Creating K8s sandbox",
            template=self._template,
            namespace=self._namespace,
            labels=self._labels,
            ttl=self._ttl,
        )

        self._sandbox = self._client.create_sandbox(**create_kwargs)

        sandbox_name = getattr(self._sandbox, "sandbox_id", None) or getattr(
            self._sandbox, "claim_name", None
        )
        if sandbox_name:
            self._sandbox_id = str(sandbox_name)

        if self._ttl is not None:
            self._apply_shutdown_time(self._sandbox_id)

        logger.info("K8s sandbox created", sandbox_id=self._sandbox_id)
        return self._sandbox

    def _apply_shutdown_time(self, sandbox_name: str) -> None:
        """Set spec.shutdownTime on the Sandbox CR for automatic cleanup.

        The SDK's ``create_sandbox()`` does not expose a TTL parameter, so we
        patch the Sandbox CR directly using the Kubernetes API.

        Tries in-cluster config first (production), then falls back to
        ``~/.kube/config`` (local dev / CI).

        Args:
            sandbox_name: Name of the Sandbox CR to patch.
        """
        from datetime import datetime, timedelta

        try:
            from kubernetes import client as k8s_client
            from kubernetes.config import ConfigException

            try:
                from kubernetes.config import load_incluster_config

                load_incluster_config()
            except ConfigException:
                from kubernetes.config import load_kube_config

                load_kube_config()

            api = k8s_client.CustomObjectsApi()
            shutdown_time = (datetime.now(UTC) + timedelta(seconds=self._ttl or 0)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            body = {"spec": {"shutdownTime": shutdown_time}}
            api.patch_namespaced_custom_object(
                group="agents.x-k8s.io",
                version="v1alpha1",
                namespace=self._namespace,
                plural="sandboxes",
                name=sandbox_name,
                body=body,
            )
            logger.info(
                "Set sandbox shutdownTime",
                sandbox_id=sandbox_name,
                shutdown_time=shutdown_time,
            )
        except Exception as e:
            logger.warning("Failed to set sandbox shutdownTime", error=str(e))

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a command inside the K8s sandbox pod.

        Commands are wrapped in ``sh -c`` so that shell features (heredocs,
        pipes, redirects, variable expansion) work correctly. The agent-sandbox
        SDK's ``commands.run()`` executes commands directly (like ``exec``),
        which does not interpret shell syntax.

        Args:
            command: Shell command to execute.
            timeout: Per-command timeout in seconds. Defaults to 300.

        Returns:
            ExecuteResponse with combined stdout+stderr, exit code, and
            truncation status.
        """
        sandbox = self._ensure_sandbox()
        effective_timeout = timeout or 300

        sh_command = f"sh -c {shlex.quote(command)}"

        try:
            result = sandbox.commands.run(sh_command, timeout=effective_timeout)
        except Exception as e:
            logger.error("K8s sandbox execute failed", error=str(e))
            return ExecuteResponse(
                output=f"Error: {e}",
                exit_code=-1,
                truncated=False,
            )

        output = result.stdout
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr

        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=False,
        )

        output = result.stdout
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr

        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the sandbox via base64 encoding through execute().

        v1 uses BaseSandbox's default approach of piping through execute().
        v2 may use the SDK's native file upload API when available.
        """
        return self._upload_files_via_execute(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox via base64 encoding through execute().

        v1 uses BaseSandbox's default approach of piping through execute().
        v2 may use the SDK's native file download API when available.
        """
        return self._download_files_via_execute(paths)

    def _upload_files_via_execute(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files by base64-encoding and piping through execute()."""
        import base64

        results: list[FileUploadResponse] = []
        for file_path, content in files:
            encoded = base64.b64encode(content).decode("ascii")
            cmd = f"mkdir -p $(dirname {file_path}) && echo '{encoded}' | base64 -d > {file_path}"
            resp = self.execute(cmd)
            if resp.exit_code == 0:
                results.append(FileUploadResponse(path=file_path))
            else:
                results.append(FileUploadResponse(path=file_path, error="is_directory"))
        return results

    def _download_files_via_execute(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files by base64-encoding through execute()."""
        import base64

        results: list[FileDownloadResponse] = []
        for file_path in paths:
            resp = self.execute(f"base64 {file_path}")
            if resp.exit_code == 0 and resp.output.strip():
                try:
                    content = base64.b64decode(resp.output.strip())
                    results.append(FileDownloadResponse(path=file_path, content=content))
                except Exception:
                    results.append(FileDownloadResponse(path=file_path, error="invalid_path"))
            else:
                results.append(FileDownloadResponse(path=file_path, error="file_not_found"))
        return results

    def terminate(self) -> None:
        """Terminate the sandbox and clean up resources.

        Safe to call multiple times. Subsequent ``execute()`` calls after
        terminate will create a new sandbox.
        """
        if self._sandbox is not None:
            try:
                self._sandbox.terminate()
                logger.info("K8s sandbox terminated", sandbox_id=self._sandbox_id)
            except Exception as e:
                logger.warning("K8s sandbox terminate failed", error=str(e))
            finally:
                self._sandbox = None
                self._client = None
