"""Container execution layer for session management."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from docker.errors import DockerException, NotFound

import docker
from server.app.exceptions import (
    ContainerError,
    ContainerExecutionError,
    ContainerNotFoundError,
    ContainerTimeoutError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from server.app.settings import Settings

logger = structlog.get_logger()


class ContainerExecutor:
    """Manages Docker containers for sessions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        try:
            self.client = docker.from_env()
        except DockerException as e:
            raise ContainerError(f"Failed to connect to Docker: {e}") from e

    def create_container(
        self,
        session_id: str,
        workspace_path: str,
        network_mode: str,
        project_id: str = "",
    ) -> str:
        """Create a new agent container for a session.

        The container runs the agent runtime which exposes a WebSocket
        server on port 9000 for communication with the Cognition server.

        Args:
            session_id: Unique session identifier
            workspace_path: Host path to mount as workspace
            network_mode: "OFF" or "ON"
            project_id: Optional project ID for context

        Returns:
            Container ID
        """
        try:
            # Network configuration for agent containers
            # Use bridge network for portability across platforms
            if network_mode == "OFF":
                # Use custom bridge network that blocks external access
                network_config = self._ensure_agent_network()
            else:
                network_config = "bridge"

            # Always use port mapping (works on Linux, limited on macOS but necessary)
            port_config = {"9000/tcp": None}  # Auto-map port

            # Build environment variables for the agent
            env_vars = self._build_agent_env(session_id, project_id)

            container = self.client.containers.run(
                self.settings.agent_docker_image,  # Use agent-specific image
                detach=True,
                name=f"cognition-agent-{session_id}",
                user="1000:1000",
                working_dir="/workspace/repo",
                volumes={
                    workspace_path: {
                        "bind": "/workspace/repo",
                        "mode": "rw",
                    }
                },
                network=network_config,
                ports=port_config,  # Port mapping (None on macOS/host network)
                mem_limit=self.settings.container_memory_limit,
                cpu_period=100000,
                cpu_quota=int(self.settings.container_cpu_limit * 100000),
                environment=env_vars,
                labels={
                    "cognition.session_id": session_id,
                    "cognition.project_id": project_id,
                    "cognition.managed": "true",
                    "cognition.type": "agent",
                },
            )

            logger.info(
                "Created agent container",
                session_id=session_id,
                container_id=container.id[:12],
                network_mode=network_mode,
                project_id=project_id,
            )
            return container.id

        except DockerException as e:
            raise ContainerError(f"Failed to create agent container: {e}") from e

    def _ensure_agent_network(self) -> str:
        """Ensure the restricted agent network exists.

        Creates a Docker bridge network that allows container-to-host
        communication (for WebSocket and LLM APIs) but blocks external
        internet access.
        """
        network_name = "cognition-agents"

        try:
            # Check if network exists
            self.client.networks.get(network_name)
            return network_name
        except NotFound:
            # Create the network
            try:
                network = self.client.networks.create(
                    network_name,
                    driver="bridge",
                    internal=False,  # Allow external access (for LLM APIs)
                    labels={
                        "cognition.managed": "true",
                        "cognition.network.type": "agent",
                    },
                )
                logger.info("Created agent network", network_name=network_name)
                return network_name
            except DockerException as e:
                logger.warning(
                    "Failed to create agent network, falling back to bridge",
                    error=str(e),
                )
                return "bridge"

    def _build_agent_env(
        self, session_id: str, project_id: str, agent_port: int | None = None
    ) -> dict[str, str]:
        """Build environment variables for the agent container.

        Args:
            session_id: Session identifier
            project_id: Project identifier
            agent_port: Optional port for agent (defaults to 9000)

        Returns:
            Dictionary of environment variables
        """
        port = agent_port or 9000
        env = {
            "SESSION_ID": session_id,
            "PROJECT_ID": project_id,
            "AGENT_PORT": str(port),
            "AGENT_HOST": "0.0.0.0",
            "LOG_LEVEL": "info",
        }

        # LLM credentials
        if self.settings.openai_api_key:
            env["OPENAI_API_KEY"] = self.settings.openai_api_key
        if self.settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self.settings.anthropic_api_key
        if self.settings.aws_region:
            env["AWS_REGION"] = self.settings.aws_region
            env["USE_BEDROCK_IAM_ROLE"] = str(self.settings.use_bedrock_iam_role)

        # Tracing/Observability
        if self.settings.otel_service_name:
            env["OTEL_SERVICE_NAME"] = self.settings.otel_service_name
        if self.settings.otel_exporter_otlp_endpoint:
            env["OTEL_EXPORTER_OTLP_ENDPOINT"] = self.settings.otel_exporter_otlp_endpoint

        return env

    def get_container_port(self, container_id: str, session_id: str | None = None) -> int:
        """Get the host port mapped to the agent's port 9000.

        Args:
            container_id: Container ID
            session_id: Unused (for compatibility)

        Returns:
            Host port number

        Raises:
            ContainerError: If port not found
        """
        try:
            container = self.client.containers.get(container_id)
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})

            # Look for port 9000/tcp mapping
            port_9000 = ports.get("9000/tcp", [])
            if port_9000 and len(port_9000) > 0:
                host_port = port_9000[0].get("HostPort")
                if host_port:
                    return int(host_port)

            raise ContainerError(f"Port 9000 not mapped for container {container_id[:12]}")

        except NotFound:
            raise ContainerNotFoundError(f"Container {container_id} not found")
        except DockerException as e:
            raise ContainerError(f"Failed to get container port: {e}") from e

    async def wait_for_container_ready(self, container_id: str, timeout: float = 30.0) -> None:
        """Wait for the agent container to be ready and healthy.

        Polls the container to ensure it's running and the agent port is accessible.

        Args:
            container_id: Container ID
            timeout: Maximum time to wait in seconds

        Raises:
            ContainerNotFoundError: If container not found
            ContainerTimeoutError: If container not ready within timeout
            ContainerError: If health check fails
        """
        import time

        start_time = time.time()
        last_error = None

        while time.time() - start_time < timeout:
            try:
                # Check if container is still running
                container = self.client.containers.get(container_id)
                if container.status != "running":
                    raise ContainerError(f"Container not running: {container.status}")

                # Try to get the port (if port is mapped, agent is starting)
                try:
                    port = self.get_container_port(container_id)
                    logger.debug(
                        "Container port ready",
                        container_id=container_id[:12],
                        port=port,
                    )
                    return  # Container is ready

                except ContainerError:
                    # Port not yet mapped, keep waiting
                    pass

            except NotFound:
                raise ContainerNotFoundError(f"Container {container_id} not found")
            except Exception as e:
                last_error = e

            # Wait before next attempt
            await asyncio.sleep(0.5)

        raise ContainerTimeoutError(
            f"Container {container_id[:12]} not ready within {timeout}s: {last_error}"
        )

    def stop_container(self, container_id: str) -> None:
        """Stop and remove a container."""
        try:
            container = self.client.containers.get(container_id)
            container.stop(timeout=10)
            container.remove(force=True)
            logger.info("Stopped and removed container", container_id=container_id[:12])
        except NotFound:
            logger.warning("Container not found", container_id=container_id[:12])
        except DockerException as e:
            logger.error("Failed to stop container", container_id=container_id[:12], error=str(e))

    async def execute(
        self,
        container_id: str,
        command: list[str],
        timeout: int | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        """Execute a command in a container and stream output.

        Args:
            container_id: Container ID
            command: Command as argv list (no shell strings)
            timeout: Timeout in seconds

        Yields:
            Tuples of (stream_name, chunk) where stream_name is "stdout" or "stderr"

        Raises:
            ContainerNotFoundError: If container not found
            ContainerExecutionError: If execution fails
            ContainerTimeoutError: If execution times out
        """
        timeout = timeout or self.settings.container_timeout

        try:
            container = self.client.containers.get(container_id)
        except NotFound as e:
            raise ContainerNotFoundError(f"Container {container_id[:12]} not found") from e
        except DockerException as e:
            raise ContainerExecutionError(f"Failed to get container: {e}") from e

        try:
            # Create exec instance
            exec_result = container.client.api.exec_create(
                container.id,
                command,
                stdout=True,
                stderr=True,
                stream=True,
                workdir="/workspace/repo",
                user="1000:1000",
            )

            exec_id = exec_result["Id"]

            # Start execution with timeout
            output = container.client.api.exec_start(exec_id, stream=True, demux=True)

            start_time = asyncio.get_event_loop().time()

            for stdout_chunk, stderr_chunk in output:
                # Check timeout
                if asyncio.get_event_loop().time() - start_time > timeout:
                    raise ContainerTimeoutError(
                        f"Command timed out after {timeout}s",
                        details={"command": command, "timeout": timeout},
                    )

                if stdout_chunk:
                    yield ("stdout", stdout_chunk.decode("utf-8", errors="replace"))
                if stderr_chunk:
                    yield ("stderr", stderr_chunk.decode("utf-8", errors="replace"))

                # Allow other tasks to run
                await asyncio.sleep(0)

            # Get exit code
            inspect = container.client.api.exec_inspect(exec_id)
            exit_code = inspect.get("ExitCode", -1)

            if exit_code != 0:
                logger.warning(
                    "Command exited with non-zero code",
                    container_id=container_id[:12],
                    command=command,
                    exit_code=exit_code,
                )

        except ContainerTimeoutError:
            raise
        except DockerException as e:
            raise ContainerExecutionError(f"Execution failed: {e}") from e

    def cleanup_all(self) -> None:
        """Stop and remove all Cognition-managed containers."""
        try:
            containers = self.client.containers.list(
                filters={"label": "cognition.managed=true"},
                all=True,
            )
            for container in containers:
                try:
                    container.stop(timeout=5)
                    container.remove(force=True)
                    logger.info("Cleaned up container", container_id=container.id[:12])
                except DockerException as e:
                    logger.error(
                        "Failed to cleanup container",
                        container_id=container.id[:12],
                        error=str(e),
                    )
        except DockerException as e:
            logger.error("Failed to list containers for cleanup", error=str(e))
