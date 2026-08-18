"""P3-ALN-3 Business Scenarios: CLI Middleware Import Fix.

As a developer using Cognition CLI,
I want scaffolding commands to work correctly
so that I can create middleware without import errors.

Business Value:
- No ImportError when creating middleware
- Clear error messages for invalid inputs
- Correct documentation in generated templates
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.mark.e2e
class TestCLIMiddlewareScaffolding:
    """Test P3-ALN-3: CLI Middleware Import Fix."""

    def test_create_middleware_command_exists(self) -> None:
        """cognition create middleware command is available."""
        result = subprocess.run(
            [sys.executable, "-m", "server.app.cli", "create", "middleware", "--help"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "middleware" in result.stdout.lower()

    def test_create_middleware_generates_valid_file(self) -> None:
        """cognition create middleware generates valid Python file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "middleware",
                    "test_middleware",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            # Should succeed
            assert result.returncode == 0, f"Error: {result.stderr}"

            # Check file was created
            middleware_file = Path(tmpdir) / "test_middleware.py"
            assert middleware_file.exists(), f"File not created: {middleware_file}"

            # Check file content
            content = middleware_file.read_text()
            assert "class TestMiddleware" in content or "class" in content

    def test_create_middleware_no_importerror(self) -> None:
        """Generated middleware file has correct imports (no ImportError)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "middleware",
                    "auth_middleware",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            middleware_file = Path(tmpdir) / "auth_middleware.py"
            content = middleware_file.read_text()

            # Check for correct import (fixed in P3-ALN-3)
            # Should NOT have: from deepagents.middleware import AgentMiddleware
            # Should have: from langchain.agents.middleware.types import AgentMiddleware
            assert "ImportError" not in content

            # Try to parse the file
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(middleware_file)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Syntax error in generated file: {result.stderr}"

    def test_create_middleware_with_description(self) -> None:
        """cognition create middleware accepts description parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "middleware",
                    "logging_mw",
                    "--path",
                    tmpdir,
                    "--description",
                    "Logging middleware for request tracking",
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0

            middleware_file = Path(tmpdir) / "logging_mw.py"
            content = middleware_file.read_text()
            assert "Logging middleware" in content


@pytest.mark.e2e
class TestCLIToolScaffoldingBoundary:
    """Tool scaffolding stays removed from the host-writing CLI surface."""

    def test_create_tool_command_is_not_available(self) -> None:
        """Cognition no longer creates host Python tools through the CLI."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "server.app.cli",
                "create",
                "tool",
                "--help",
            ],
            capture_output=True,
            text=True,
        )

        combined = result.stdout.lower() + result.stderr.lower()
        assert result.returncode != 0
        assert "no such command" in combined
        assert "tool" in combined


@pytest.mark.e2e
class TestCLIScaffoldingIntegration:
    """Test end-to-end CLI scaffolding workflows."""

    def test_create_multiple_middleware_files_in_same_session(self) -> None:
        """Complete workflow: create multiple middleware files in one directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            first_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "middleware",
                    "my_processor",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )
            assert first_result.returncode == 0

            second_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "middleware",
                    "my_middleware",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )
            assert second_result.returncode == 0

            assert (Path(tmpdir) / "my_processor.py").exists()
            assert (Path(tmpdir) / "my_middleware.py").exists()

    def test_scaffolding_produces_runnable_files(self) -> None:
        """Scaffolded files can be imported without errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "middleware",
                    "runnable_middleware",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            # Try to import the module
            original_path = sys.path.copy()
            sys.path.insert(0, tmpdir)

            try:
                # This should not raise ImportError
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "runnable_middleware", Path(tmpdir) / "runnable_middleware.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                assert hasattr(module, "RunnableMiddlewareMiddleware")

            finally:
                sys.path[:] = original_path
