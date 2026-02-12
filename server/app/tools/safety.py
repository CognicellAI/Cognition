"""Safety validation for tool execution."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from server.app.exceptions import PathValidationError, ToolValidationError

if TYPE_CHECKING:
    from server.app.sessions.manager import SessionManager


class SafetyValidator:
    """Validates tool requests for safety."""

    # Dangerous shell metacharacters
    SHELL_METACHARACTERS = re.compile(r"[;&|`$(){}[\]\\<>]")

    # Path traversal patterns
    PATH_TRAVERSAL = re.compile(r"\.\.(?:/|\\\\)")

    def __init__(self, session_manager: SessionManager) -> None:
        self.session_manager = session_manager

    def validate_argv_only(self, cmd: list[str]) -> None:
        """Ensure command uses argv only (no shell strings).

        Args:
            cmd: Command as argv list

        Raises:
            ToolValidationError: If shell strings detected
        """
        if not isinstance(cmd, list):
            raise ToolValidationError(
                "Command must be a list (argv)",
                details={"cmd": cmd},
            )

        for arg in cmd:
            if not isinstance(arg, str):
                raise ToolValidationError(
                    "Command arguments must be strings",
                    details={"cmd": cmd},
                )

            if self.SHELL_METACHARACTERS.search(arg):
                raise ToolValidationError(
                    f"Shell metacharacters detected in argument: {arg}",
                    details={"cmd": cmd, "arg": arg},
                )

    def validate_path(self, session_id: str, path: str) -> None:
        """Validate that a path is safe and within workspace.

        Args:
            session_id: The session ID
            path: Path to validate

        Raises:
            PathValidationError: If path is unsafe
        """
        if not path:
            raise PathValidationError("Path cannot be empty")

        # Check for path traversal
        if self.PATH_TRAVERSAL.search(path):
            raise PathValidationError(
                f"Path traversal detected: {path}",
                details={"path": path, "session_id": session_id},
            )

        # Check for absolute paths outside workspace
        if path.startswith("/") and not path.startswith("/workspace/"):
            raise PathValidationError(
                f"Absolute path outside workspace not allowed: {path}",
                details={"path": path, "session_id": session_id},
            )

        # Validate using workspace manager
        workspace_manager = self.session_manager.workspace_manager
        workspace_manager.validate_path_in_workspace(session_id, path)

    def validate_diff(self, diff: str) -> None:
        """Validate a unified diff patch.

        Args:
            diff: Diff string to validate

        Raises:
            ToolValidationError: If diff is invalid or suspicious
        """
        if not diff:
            raise ToolValidationError("Diff cannot be empty")

        # Check for basic unified diff format
        if not any(line.startswith(("---", "+++", "@@", "-", "+")) for line in diff.split("\n")):
            raise ToolValidationError(
                "Invalid diff format: expected unified diff",
                details={"diff_preview": diff[:200]},
            )

        # Check for suspicious patterns (binary files, device files, etc.)
        suspicious_patterns = [
            "/dev/",
            "/proc/",
            "/sys/",
            "mode 100755",  # executable permissions (might be okay, but flag)
        ]

        for pattern in suspicious_patterns:
            if pattern in diff:
                # Just log warning for now, don't block
                import structlog

                logger = structlog.get_logger()
                logger.warning(
                    "Suspicious pattern in diff",
                    pattern=pattern,
                    diff_preview=diff[:200],
                )
