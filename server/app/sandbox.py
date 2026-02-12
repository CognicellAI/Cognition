"""Sandbox backends for executing commands."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ExecuteResult:
    """Result of a command execution."""

    output: str
    exit_code: int


class LocalSandbox:
    """Sandbox backend that executes commands on the local filesystem.

    This is the simplest sandbox implementation for local development.
    Commands run via subprocess in the configured root directory.

    Example:
        >>> sandbox = LocalSandbox(root_dir="/home/user/my-project")
        >>> result = sandbox.execute("ls -la")
        >>> print(result.output)
        >>> print(result.exit_code)
    """

    def __init__(self, root_dir: str | Path):
        """Initialize the sandbox with a root directory.

        Args:
            root_dir: The directory where all commands will be executed.
                     Must be an absolute path.
        """
        self.root_dir = Path(root_dir).resolve()
        if not self.root_dir.exists():
            self.root_dir.mkdir(parents=True, exist_ok=True)

    def execute(
        self,
        command: str,
        timeout: Optional[float] = 300.0,
        env: Optional[dict[str, str]] = None,
    ) -> ExecuteResult:
        """Execute a command in the sandbox.

        Args:
            command: The shell command to execute.
            timeout: Maximum time to wait for command completion (seconds).
                    Default is 300 seconds (5 minutes).
            env: Optional environment variables to set for the command.

        Returns:
            ExecuteResult containing stdout/stderr combined and exit code.

        Raises:
            subprocess.TimeoutExpired: If the command times out.
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=self.root_dir,
                timeout=timeout,
                env=env,
            )

            # Combine stdout and stderr
            output = result.stdout
            if result.stderr:
                if output:
                    output += "\n"
                output += result.stderr

            return ExecuteResult(
                output=output,
                exit_code=result.returncode,
            )

        except subprocess.TimeoutExpired as e:
            output = e.stdout.decode() if e.stdout else ""
            if e.stderr:
                if output:
                    output += "\n"
                output += e.stderr.decode()
            if output:
                output += "\n"
            output += f"Command timed out after {timeout} seconds"

            return ExecuteResult(
                output=output,
                exit_code=-1,
            )

    def __repr__(self) -> str:
        return f"LocalSandbox(root_dir={self.root_dir})"
