"""Unit tests for the sandbox module."""

import pytest
import tempfile
from pathlib import Path

from server.app.execution.sandbox import LocalSandbox


class TestLocalSandbox:
    """Test suite for LocalSandbox."""

    @pytest.fixture
    def sandbox(self):
        """Create a sandbox in a temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield LocalSandbox(root_dir=tmpdir)

    def test_execute_echo(self, sandbox):
        """Test basic command execution."""
        result = sandbox.execute("echo 'hello world'")
        assert result.exit_code == 0
        assert "hello world" in result.output

    def test_execute_cwd(self, sandbox):
        """Test commands run in the correct working directory."""
        result = sandbox.execute("pwd")
        assert str(sandbox.root_dir) in result.output

    def test_execute_timeout(self, sandbox):
        """Test timeout enforcement."""
        result = sandbox.execute("sleep 10", timeout=0.1)
        assert result.exit_code == -1
        assert "timed out" in result.output.lower()

    def test_execute_failure(self, sandbox):
        """Test non-zero exit codes."""
        result = sandbox.execute("bash -c 'exit 42'")
        assert result.exit_code == 42

    def test_create_and_list_file(self, sandbox):
        """Test creating and listing files."""
        # Create a file using Python (shell redirects don't work without shell=True)
        test_file = Path(sandbox.root_dir) / "test.txt"
        test_file.write_text("test content")

        # List files
        result = sandbox.execute("ls -la")
        assert result.exit_code == 0
        assert "test.txt" in result.output

    def test_read_file(self, sandbox):
        """Test reading file contents."""
        # Create a file
        test_file = Path(sandbox.root_dir) / "test.txt"
        test_file.write_text("Hello from sandbox")

        # Read via execute
        result = sandbox.execute("cat test.txt")
        assert result.exit_code == 0
        assert "Hello from sandbox" in result.output

    def test_shell_injection_prevented(self, sandbox):
        """Test that shell metacharacters are not interpreted (security)."""
        # Create a file with a name that looks like a command injection attempt
        # With shell=True, this would execute 'id' command
        # With shell=False, this creates a file literally named ";id"
        result = sandbox.execute(["echo", ";id"])
        assert result.exit_code == 0
        # Output should be the literal string ";id", not the result of id command
        assert ";id" in result.output
        assert "uid=" not in result.output  # Would appear if 'id' command ran

    def test_command_as_list(self, sandbox):
        """Test passing command as argument list (recommended for security)."""
        result = sandbox.execute(["echo", "hello world"])
        assert result.exit_code == 0
        assert "hello world" in result.output
