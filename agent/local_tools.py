"""Local tools for agent container.

Tools that execute locally inside the container using subprocess.
These replace the server-side ContainerExecutor bridging.
"""

from __future__ import annotations

import subprocess
from typing import Any

import structlog
from langchain_core.tools import Tool

logger = structlog.get_logger()


class GitTools:
    """Git operations running locally in the container."""

    def __init__(self, working_dir: str = "/workspace/repo") -> None:
        self.working_dir = working_dir

    def _run_git(self, args: list[str]) -> tuple[int, str, str]:
        """Run a git command and return (exit_code, stdout, stderr)."""
        try:
            result = subprocess.run(
                ["git"] + args,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.working_dir,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Git command timed out"
        except Exception as e:
            return -1, "", str(e)

    def git_status(self) -> str:
        """Get git status --porcelain output."""
        exit_code, stdout, stderr = self._run_git(["status", "--porcelain"])
        if exit_code != 0:
            return f"Error: {stderr}"
        return stdout if stdout else "No changes"

    def git_diff(self, staged: bool = False) -> str:
        """Get git diff output."""
        args = ["diff"]
        if staged:
            args.append("--staged")
        exit_code, stdout, stderr = self._run_git(args)
        if exit_code != 0:
            return f"Error: {stderr}"
        return stdout if stdout else "No differences"

    @property
    def tools(self) -> list[Tool]:
        """Return LangChain Tool instances."""
        return [
            Tool(
                name="git_status",
                func=self.git_status,
                description="Get the current git status of the repository. Shows modified, added, and deleted files.",
            ),
            Tool(
                name="git_diff",
                func=lambda staged=False: self.git_diff(staged),
                description="Show differences between working directory and HEAD. Set staged=True to see staged changes.",
            ),
        ]


class TestTools:
    """Test execution running locally in the container."""

    def __init__(self, working_dir: str = "/workspace/repo") -> None:
        self.working_dir = working_dir

    def run_tests(self, cmd: str = "pytest -q") -> str:
        """Run tests using pytest or other test runner.

        Args:
            cmd: Test command (default: "pytest -q")

        Returns:
            Test output or error message
        """
        # Parse command into args (handle quoted strings)
        import shlex

        try:
            args = shlex.split(cmd)
        except ValueError:
            # If parsing fails, treat as single arg
            args = [cmd]

        logger.info("Running tests", cmd=cmd, working_dir=self.working_dir)

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self.working_dir,
            )

            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr if output else result.stderr

            # Include exit code info
            if result.returncode != 0:
                output += f"\n\nExit code: {result.returncode}"

            return output

        except subprocess.TimeoutExpired:
            return "Test execution timed out after 300 seconds"
        except Exception as e:
            return f"Test execution failed: {e}"

    @property
    def tools(self) -> list[Tool]:
        """Return LangChain Tool instances."""
        return [
            Tool(
                name="run_tests",
                func=self.run_tests,
                description="Run tests using pytest or another test runner. Accepts a command string like 'pytest -q' or 'pytest tests/test_specific.py -v'.",
            ),
        ]


# Convenience factory functions
def create_git_tools(working_dir: str = "/workspace/repo") -> GitTools:
    """Create GitTools instance."""
    return GitTools(working_dir)


def create_test_tools(working_dir: str = "/workspace/repo") -> TestTools:
    """Create TestTools instance."""
    return TestTools(working_dir)
