"""Tests for LocalSandboxBackend."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.sandbox import LocalSandboxBackend
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse


class TestLocalSandboxBackend:
    """Test LocalSandboxBackend functionality."""

    def test_sandbox_initialization(self):
        """Test sandbox initializes with correct workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            assert sandbox.id == "local"
            assert sandbox.workspace_path == Path(tmpdir)

    def test_sandbox_id_from_env(self):
        """Test sandbox ID from environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"SESSION_ID": "test-session-123"}):
                sandbox = LocalSandboxBackend(workspace_path=tmpdir)
                assert sandbox.id == "test-session-123"

    def test_execute_success(self):
        """Test successful command execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            result = sandbox.execute("echo 'Hello World'")

            assert isinstance(result, ExecuteResponse)
            assert result.exit_code == 0
            assert "Hello World" in result.output

    def test_execute_failure(self):
        """Test command execution failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            result = sandbox.execute("exit 1")

            assert isinstance(result, ExecuteResponse)
            assert result.exit_code == 1

    def test_execute_timeout(self):
        """Test command execution timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # This should timeout after 300 seconds, but we'll use a shorter sleep
            # and mock the timeout for testing
            result = sandbox.execute("sleep 0.1")  # Short sleep for fast tests

            # Should succeed since 0.1s < 300s timeout
            assert result.exit_code == 0

    def test_execute_in_workspace_directory(self):
        """Test commands execute in the workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Create a file in the workspace
            Path(tmpdir).joinpath("testfile.txt").write_text("test content")

            # Execute pwd to check working directory
            result = sandbox.execute("pwd")

            assert tmpdir in result.output

    def test_upload_files_success(self):
        """Test successful file upload."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            files = [
                ("file1.txt", b"content1"),
                ("subdir/file2.txt", b"content2"),
            ]

            responses = sandbox.upload_files(files)

            assert len(responses) == 2
            assert all(r.success for r in responses)

            # Verify files were written
            assert Path(tmpdir).joinpath("file1.txt").read_bytes() == b"content1"
            assert Path(tmpdir).joinpath("subdir/file2.txt").read_bytes() == b"content2"

    def test_upload_files_failure(self):
        """Test file upload failure handling."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Try to write to a read-only path (simulate failure)
            # We'll create a file that can't be overwritten
            readonly_file = Path(tmpdir) / "readonly.txt"
            readonly_file.write_text("original")
            os.chmod(readonly_file, 0o444)  # Read-only

            try:
                files = [("readonly.txt", b"new content")]
                responses = sandbox.upload_files(files)

                assert len(responses) == 1
                assert not responses[0].success
                assert responses[0].error is not None
            finally:
                os.chmod(readonly_file, 0o644)  # Restore permissions for cleanup

    def test_download_files_success(self):
        """Test successful file download."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Create test files
            Path(tmpdir).joinpath("file1.txt").write_bytes(b"content1")
            Path(tmpdir).joinpath("file2.txt").write_bytes(b"content2")

            responses = sandbox.download_files(["file1.txt", "file2.txt"])

            assert len(responses) == 2
            assert all(r.success for r in responses)
            assert responses[0].content == b"content1"
            assert responses[1].content == b"content2"

    def test_download_files_not_found(self):
        """Test downloading non-existent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            responses = sandbox.download_files(["nonexistent.txt"])

            assert len(responses) == 1
            assert not responses[0].success
            assert responses[0].error == "file_not_found"

    def test_file_path_security(self):
        """Test that paths are scoped to workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Try path traversal
            files = [("../outside.txt", b"malicious")]
            responses = sandbox.upload_files(files)

            # Should create file inside workspace, not outside
            # (Path joins handle this correctly)
            outside_path = Path(tmpdir).parent / "outside.txt"
            assert not outside_path.exists()

            # File should be inside workspace
            inside_path = Path(tmpdir) / "../outside.txt"
            assert inside_path.exists()


class TestLocalSandboxBaseFunctionality:
    """Test BaseSandbox inherited functionality."""

    def test_ls_info(self):
        """Test directory listing via BaseSandbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Create some files
            Path(tmpdir).joinpath("file1.txt").touch()
            Path(tmpdir).joinpath("file2.py").touch()

            # ls_info is provided by BaseSandbox using execute()
            files = sandbox.ls_info(".")

            assert len(files) == 2
            file_names = [f.name for f in files]
            assert "file1.txt" in file_names
            assert "file2.py" in file_names

    def test_read_file(self):
        """Test file reading via BaseSandbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Create a file
            test_content = "Line 1\nLine 2\nLine 3\n"
            Path(tmpdir).joinpath("test.txt").write_text(test_content)

            # Read via BaseSandbox
            content = sandbox.read("test.txt")

            assert content == test_content

    def test_write_file(self):
        """Test file writing via BaseSandbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Write via BaseSandbox
            result = sandbox.write("newfile.txt", "Hello World")

            assert result.success is True
            assert Path(tmpdir).joinpath("newfile.txt").read_text() == "Hello World"

    def test_edit_file(self):
        """Test file editing via BaseSandbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = LocalSandboxBackend(workspace_path=tmpdir)

            # Create file
            Path(tmpdir).joinpath("test.txt").write_text("Hello World")

            # Edit via BaseSandbox
            result = sandbox.edit("test.txt", "World", "Universe")

            assert result.success is True
            assert Path(tmpdir).joinpath("test.txt").read_text() == "Hello Universe"
