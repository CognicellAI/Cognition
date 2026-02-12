"""MVP allowlist policies and tool validation for agents."""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

logger = structlog.get_logger()


class ToolPolicy:
    """Defines allowed tools and validates tool requests."""

    # MVP allowlist of permitted tools
    ALLOWED_TOOLS: ClassVar[set[str]] = {
        "read_file",
        "read_files",
        "search",
        "apply_patch",
        "run_tests",
        "git_status",
        "git_diff",
        "ask_user",
    }

    # Tools that can be invoked without explicit user approval
    SAFE_TOOLS: ClassVar[set[str]] = {
        "read_file",
        "read_files",
        "search",
        "git_status",
        "git_diff",
        "ask_user",
    }

    # Tools that modify state and may require approval
    RISKY_TOOLS: ClassVar[set[str]] = {
        "apply_patch",
        "run_tests",
    }

    @classmethod
    def is_allowed(cls, tool_name: str) -> bool:
        """Check if a tool is in the allowlist.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is allowed
        """
        return tool_name in cls.ALLOWED_TOOLS

    @classmethod
    def is_safe(cls, tool_name: str) -> bool:
        """Check if a tool is considered safe (read-only).

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is safe
        """
        return tool_name in cls.SAFE_TOOLS

    @classmethod
    def is_risky(cls, tool_name: str) -> bool:
        """Check if a tool modifies state.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is risky
        """
        return tool_name in cls.RISKY_TOOLS

    @classmethod
    def validate_tool_request(cls, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Validate a tool request against policies.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            True if request is valid

        Raises:
            ValueError: If tool not allowed or arguments invalid
        """
        if not cls.is_allowed(tool_name):
            raise ValueError(
                f"Tool '{tool_name}' is not in the allowlist. "
                f"Allowed tools: {', '.join(sorted(cls.ALLOWED_TOOLS))}"
            )

        # Validate specific tool arguments
        if tool_name == "apply_patch":
            cls._validate_apply_patch(arguments)
        elif tool_name == "run_tests":
            cls._validate_run_tests(arguments)
        elif tool_name == "search":
            cls._validate_search(arguments)

        return True

    @staticmethod
    def _validate_apply_patch(arguments: dict[str, Any]) -> None:
        """Validate apply_patch arguments."""
        if "diff" not in arguments:
            raise ValueError("apply_patch requires 'diff' argument")

        diff = arguments["diff"]
        if not isinstance(diff, str):
            raise ValueError("apply_patch 'diff' must be a string")

        if not diff.strip():
            raise ValueError("apply_patch 'diff' cannot be empty")

        # Basic check for unified diff format
        if not any(line.startswith(("---", "+++", "@@", "-", "+")) for line in diff.split("\n")):
            raise ValueError("apply_patch 'diff' must be a valid unified diff")

    @staticmethod
    def _validate_run_tests(arguments: dict[str, Any]) -> None:
        """Validate run_tests arguments."""
        if "cmd" in arguments:
            cmd = arguments["cmd"]
            if not isinstance(cmd, list):
                raise ValueError("run_tests 'cmd' must be a list")

            # Ensure no shell metacharacters
            for arg in cmd:
                if not isinstance(arg, str):
                    raise ValueError("run_tests command arguments must be strings")

                # Check for dangerous characters
                dangerous = {";", "|", "&", "`", "$", "(", ")", "{", "}", "[", "]", "<", ">"}
                if any(c in arg for c in dangerous):
                    raise ValueError(f"run_tests argument contains dangerous character: {arg}")

    @staticmethod
    def _validate_search(arguments: dict[str, Any]) -> None:
        """Validate search arguments."""
        if "query" not in arguments:
            raise ValueError("search requires 'query' argument")

        query = arguments["query"]
        if not isinstance(query, str):
            raise ValueError("search 'query' must be a string")

        if not query.strip():
            raise ValueError("search 'query' cannot be empty")

        # Validate max_results if provided
        if "max_results" in arguments:
            max_results = arguments["max_results"]
            if not isinstance(max_results, int):
                raise ValueError("search 'max_results' must be an integer")
            if max_results < 1 or max_results > 1000:
                raise ValueError("search 'max_results' must be between 1 and 1000")


class SessionPolicy:
    """Policies for session management."""

    MAX_TURNS = 50  # Maximum conversation turns per session
    MAX_TOOL_CALLS = 100  # Maximum tool calls per session

    @classmethod
    def validate_turn_count(cls, turn_count: int) -> None:
        """Validate session hasn't exceeded max turns.

        Args:
            turn_count: Current turn count

        Raises:
            RuntimeError: If max turns exceeded
        """
        if turn_count >= cls.MAX_TURNS:
            raise RuntimeError(
                f"Session exceeded maximum turns ({cls.MAX_TURNS}). Please start a new session."
            )

    @classmethod
    def validate_tool_call_count(cls, tool_call_count: int) -> None:
        """Validate session hasn't exceeded max tool calls.

        Args:
            tool_call_count: Current tool call count

        Raises:
            RuntimeError: If max tool calls exceeded
        """
        if tool_call_count >= cls.MAX_TOOL_CALLS:
            raise RuntimeError(
                f"Session exceeded maximum tool calls ({cls.MAX_TOOL_CALLS}). "
                "Please start a new session."
            )
