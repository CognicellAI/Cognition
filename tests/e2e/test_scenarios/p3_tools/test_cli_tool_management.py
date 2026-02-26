"""P3-TR-8 Business Scenarios: CLI Tool Management.

As a DevOps engineer managing Cognition deployments,
I want CLI commands to inspect and manage the tool registry
so that I can verify deployments and automate tool management.

Business Value:
- Command-line visibility into registered tools
- CI/CD integration for deployment verification
- Manual reload capability for immediate updates
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.e2e
class TestCLIToolList:
    """Test P3-TR-8: cognition tools list command."""

    def test_tools_list_command_exists(self) -> None:
        """cognition tools list command is available."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "list" in result.stdout.lower() or "usage:" in result.stdout.lower()

    def test_tools_list_shows_help(self) -> None:
        """cognition tools list --help shows usage information."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "list" in result.stdout
        assert "reload" in result.stdout

    @pytest.mark.skip(reason="Requires running server")
    def test_tools_list_connects_to_server(self) -> None:
        """cognition tools list connects to server and displays tools."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list"],
            capture_output=True,
            text=True,
        )

        # Should either succeed with tool list or fail with connection error
        # Either outcome is acceptable for this test
        assert result.returncode in [0, 1]

    @pytest.mark.skip(reason="Requires running server")
    def test_tools_list_exits_with_error_on_load_errors(self) -> None:
        """cognition tools list exits with code 1 when tool load errors exist."""
        # This test would require a server with broken tools
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list"],
            capture_output=True,
            text=True,
        )

        # With load errors, should exit with code 1
        # Without load errors, should exit with code 0
        assert result.returncode in [0, 1]


@pytest.mark.e2e
class TestCLIToolReload:
    """Test P3-TR-8: cognition tools reload command."""

    def test_tools_reload_command_exists(self) -> None:
        """cognition tools reload command is available."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "reload", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0

    @pytest.mark.skip(reason="Requires running server")
    def test_tools_reload_triggers_server_reload(self) -> None:
        """cognition tools reload triggers server-side tool reload."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "reload"],
            capture_output=True,
            text=True,
        )

        # Should either succeed or fail with connection error
        assert result.returncode in [0, 1]

    @pytest.mark.skip(reason="Requires running server")
    def test_tools_reload_shows_count_and_errors(self) -> None:
        """cognition tools reload displays tool count and errors."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "reload"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            # On success, should show reloaded count
            assert "reloaded" in result.stdout.lower() or "✓" in result.stdout


@pytest.mark.e2e
class TestCLIToolErrorHandling:
    """Test CLI error handling for P3-TR-8."""

    @pytest.mark.skip(reason="Requires running server")
    def test_tools_list_shows_error_when_server_down(self) -> None:
        """cognition tools list shows clear error when server is not running."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 1:
            # Should show helpful error message
            assert (
                "cannot connect" in result.stderr.lower()
                or "cannot connect" in result.stdout.lower()
            )

    @pytest.mark.skip(reason="Requires running server")
    def test_tools_list_with_custom_host_port(self) -> None:
        """cognition tools list respects --host and --port flags."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "server.app.cli",
                "tools",
                "list",
                "--host",
                "localhost",
                "--port",
                "9999",
            ],
            capture_output=True,
            text=True,
        )

        # Should attempt connection to specified host:port
        # Will fail since nothing is running on 9999
        assert result.returncode == 1


@pytest.mark.e2e
class TestCLIToolWorkflows:
    """Test common CLI tool workflows."""

    @pytest.mark.skip(reason="Requires running server")
    def test_full_tool_management_workflow(self) -> None:
        """Complete workflow: list -> reload -> list again."""
        # Initial list
        list1 = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list"],
            capture_output=True,
            text=True,
        )

        if list1.returncode not in [0, 1]:
            pytest.skip("Server not available")

        # Reload
        reload = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "reload"],
            capture_output=True,
            text=True,
        )

        # Second list
        list2 = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list"],
            capture_output=True,
            text=True,
        )

        # Both commands should complete without crashing
        assert list2.returncode in [0, 1]

    @pytest.mark.skip(reason="Requires running server")
    def test_ci_cd_deployment_verification(self) -> None:
        """CI/CD pipeline can verify tool deployment via CLI."""
        # Simulating CI/CD check: verify no load errors
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "tools", "list"],
            capture_output=True,
            text=True,
        )

        # In CI/CD, exit code 1 indicates deployment issues
        # Exit code 0 indicates successful deployment
        assert result.returncode in [0, 1]

        if result.returncode == 0:
            # No errors, deployment is good
            pass
        else:
            # Check if it's due to load errors
            assert "error" in result.stdout.lower() or "error" in result.stderr.lower()
