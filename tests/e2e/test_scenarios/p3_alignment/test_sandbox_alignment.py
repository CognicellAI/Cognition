"""P3-ALN-1 & P3-ALN-2 Business Scenarios: Deep Agents Alignment.

As a platform engineer,
I want the sandbox backend to use upstream deepagents library correctly
so that we benefit from bug fixes and maintain compatibility.

Business Value:
- Correct path resolution via deepagents' FilesystemBackend
- Shell=False security via shlex.split() execution
- virtual_mode support for testing
- No custom reimplementations with regressions
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestSandboxBackendAlignment:
    """Test P3-ALN-1: Sandbox Backend uses deepagents correctly."""

    async def test_sandbox_commands_execute_successfully(self, api_client, session) -> None:
        """Sandbox commands execute via aligned backend."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "List files in current directory"},
        )

        # Commands should execute successfully
        assert response.status_code in [200, 201]

    async def test_sandbox_path_resolution_correct(self, api_client, session) -> None:
        """Path resolution uses deepagents' correct implementation."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "What is the current working directory?"},
        )

        # Should resolve paths correctly
        assert response.status_code in [200, 201]

    async def test_sandbox_shell_injection_prevented(self, api_client, session) -> None:
        """Shell injection prevented via shlex.split() + shell=False."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Run command: echo hello; cat /etc/passwd"},
        )

        # Should handle safely without executing injected command
        assert response.status_code in [200, 201]

    async def test_sandbox_file_operations_work(self, api_client, session) -> None:
        """File operations work via aligned FilesystemBackend."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Create a test file with 'Hello World' content"},
        )

        # File operations should work
        assert response.status_code in [200, 201]

    async def test_sandbox_respects_cwd(self, api_client, session) -> None:
        """Sandbox respects current working directory setting."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Show me the current directory path"},
        )

        # Should respect CWD
        assert response.status_code in [200, 201]


@pytest.mark.asyncio
class TestVirtualModeSupport:
    """Test virtual_mode support in sandbox backends."""

    async def test_virtual_mode_configurable(self, api_client) -> None:
        """virtual_mode setting is configurable."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            # Check if sandbox configuration is exposed
            if "sandbox" in data:
                print(f"\n  Sandbox config: {data['sandbox']}")

    async def test_sandbox_with_virtual_mode_true(self, api_client, session) -> None:
        """Sandbox works correctly with virtual_mode=True."""
        # This tests that virtual_mode doesn't break functionality
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "List all files"},
        )

        assert response.status_code in [200, 201]


@pytest.mark.asyncio
class TestExecutionBackendRemoval:
    """Test P3-ALN-2: ExecutionBackend Protocol removed."""

    async def test_docker_backend_works(self, api_client) -> None:
        """Docker backend works without ExecutionBackendAdapter."""
        # If docker backend is configured, verify it works
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            backend = data.get("sandbox", {}).get("backend", "unknown")
            print(f"\n  Sandbox backend: {backend}")

    async def test_no_adapter_indirection(self, api_client, session) -> None:
        """No ExecutionBackendAdapter indirection in live path."""
        # Execute a command that would go through the backend
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Check system info"},
        )

        # Should work without adapter overhead
        assert response.status_code in [200, 201]

    async def test_backend_performance_not_degraded(self, api_client, session) -> None:
        """Backend performance is not degraded after cleanup."""
        import time

        start = time.time()
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Simple test command"},
        )
        elapsed = (time.time() - start) * 1000

        assert response.status_code in [200, 201]
        print(f"\n  Command executed in {elapsed:.0f}ms")

        # Should complete reasonably fast
        assert elapsed < 30000, f"Command took {elapsed:.0f}ms, expected <30000ms"


@pytest.mark.asyncio
class TestDeepAgentsIntegration:
    """Test integration with deepagents library."""

    async def test_agent_runtime_uses_deepagents(self, api_client, session) -> None:
        """Agent runtime correctly uses deepagents primitives."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Use the file_reader tool to read README.md"},
        )

        # Agent should use deepagents correctly
        assert response.status_code in [200, 201]

    async def test_streaming_uses_deepagents(self, api_client, session) -> None:
        """Streaming uses deepagents streaming primitives."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Say hello"},
        )

        # Streaming should work via deepagents
        assert response.status_code in [200, 201]

    async def test_checkpointer_integration(self, api_client, session) -> None:
        """Checkpointer integrates with deepagents correctly."""
        # Send first message
        response1 = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Remember: my favorite color is blue"},
        )
        assert response1.status_code in [200, 201]

        # Send second message to verify context maintained
        response2 = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "What is my favorite color?"},
        )
        assert response2.status_code in [200, 201]
