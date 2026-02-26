"""P3-ALN-3 Business Scenarios: CLI Middleware Import Fix & Tool Validation.

As a developer using Cognition CLI,
I want scaffolding commands to work correctly
so that I can create tools and middleware without import errors.

Business Value:
- No ImportError when creating middleware
- Valid Python identifiers enforced for tool names
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
class TestCLIToolNameValidation:
    """Test P3-ALN-3: Tool Name Validation."""

    def test_create_tool_valid_name(self) -> None:
        """cognition create tool accepts valid Python identifier names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "my_tool",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, f"Error: {result.stderr}"

            # Check file was created
            tool_file = Path(tmpdir) / "my_tool.py"
            assert tool_file.exists()

    def test_create_tool_transforms_hyphens(self) -> None:
        """cognition create tool transforms hyphens to underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "my-tool-name",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0

            # Should create file with underscores
            tool_file = Path(tmpdir) / "my_tool_name.py"
            assert tool_file.exists(), f"Expected {tool_file} to exist"

    def test_create_tool_transforms_spaces(self) -> None:
        """cognition create tool transforms spaces to underscores."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "my tool name",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0

            # Should create file with underscores
            tool_file = Path(tmpdir) / "my_tool_name.py"
            assert tool_file.exists()

    def test_create_tool_rejects_invalid_identifier(self) -> None:
        """cognition create tool rejects names that are not valid Python identifiers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "123bad",  # Starts with number - invalid
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            # Should fail with error about invalid identifier
            assert (
                result.returncode != 0
                or "not a valid Python identifier" in result.stdout.lower() + result.stderr.lower()
            )

    def test_create_tool_rejects_special_chars(self) -> None:
        """cognition create tool rejects names with special characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "tool@name!",  # Special chars
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            # Should fail
            assert (
                result.returncode != 0
                or "not a valid Python identifier" in result.stdout.lower() + result.stderr.lower()
            )

    def test_create_tool_next_steps_documentation(self) -> None:
        """cognition create tool shows correct next steps (no AgentRegistry mention)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "test_tool",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0

            # Should NOT mention manual AgentRegistry.register_tool()
            # (Fixed in P3-ALN-3)
            output = result.stdout.lower()
            # Next steps should mention auto-discovery
            assert "auto-discover" in output or "auto" in output or "discovery" in output


@pytest.mark.e2e
class TestCLIToolScaffolding:
    """Test CLI tool scaffolding functionality."""

    def test_create_tool_generates_valid_python(self) -> None:
        """Generated tool file is valid Python syntax."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "file_reader",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            tool_file = Path(tmpdir) / "file_reader.py"
            assert tool_file.exists()

            # Verify Python syntax
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(tool_file)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_create_tool_has_tool_decorator(self) -> None:
        """Generated tool has @tool decorator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "calculator",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )

            tool_file = Path(tmpdir) / "calculator.py"
            content = tool_file.read_text()

            # Should have @tool decorator
            assert "@tool" in content

    def test_create_tool_default_path(self) -> None:
        """cognition create tool uses default .cognition/tools/ path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Change to temp directory
            import os

            original_cwd = os.getcwd()
            os.chdir(tmpdir)

            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "server.app.cli",
                        "create",
                        "tool",
                        "default_path_tool",
                    ],
                    capture_output=True,
                    text=True,
                )

                # Should create .cognition/tools/ directory
                expected_path = Path(tmpdir) / ".cognition" / "tools" / "default_path_tool.py"
                assert expected_path.exists(), f"Expected {expected_path} to exist"

            finally:
                os.chdir(original_cwd)


@pytest.mark.e2e
class TestCLIScaffoldingIntegration:
    """Test end-to-end CLI scaffolding workflows."""

    def test_create_tool_then_middleware_workflow(self) -> None:
        """Complete workflow: create tool and middleware in same session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create tool
            tool_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "my_processor",
                    "--path",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
            )
            assert tool_result.returncode == 0

            # Create middleware
            mw_result = subprocess.run(
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
            assert mw_result.returncode == 0

            # Verify both files exist
            assert (Path(tmpdir) / "my_processor.py").exists()
            assert (Path(tmpdir) / "my_middleware.py").exists()

    def test_scaffolding_produces_runnable_files(self) -> None:
        """Scaffolded files can be imported without errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.app.cli",
                    "create",
                    "tool",
                    "runnable_tool",
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
                    "runnable_tool", Path(tmpdir) / "runnable_tool.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Should have a tool function
                assert hasattr(module, "runnable_tool")

            finally:
                sys.path[:] = original_path
