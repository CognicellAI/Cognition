"""Enhanced tool system for Phase 4.

Additional tools beyond what deepagents provides:
- Git integration
- Web search
- Code search
- Test runner integration
- Linter integration
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from server.app.sandbox import LocalSandbox


class ToolCategory(Enum):
    """Categories of tools."""

    FILESYSTEM = "filesystem"
    GIT = "git"
    SEARCH = "search"
    TEST = "test"
    LINT = "lint"
    WEB = "web"


@dataclass
class Tool:
    """Definition of a tool."""

    name: str
    category: ToolCategory
    description: str
    func: Callable[..., Any]
    requires_confirmation: bool = False


class ToolRegistry:
    """Registry of available tools.

    Extends deepagents' built-in tools with additional capabilities.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._categories: dict[ToolCategory, list[str]] = {cat: [] for cat in ToolCategory}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._categories[tool.category].append(tool.name)

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self, category: Optional[ToolCategory] = None) -> list[Tool]:
        """List all tools, optionally filtered by category."""
        if category:
            return [self._tools[name] for name in self._categories[category]]
        return list(self._tools.values())

    def call(self, name: str, **kwargs) -> Any:
        """Execute a tool by name."""
        tool = self.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        return tool.func(**kwargs)


# ============================================================================
# Git Tools
# ============================================================================


class GitTools:
    """Git integration tools."""

    def __init__(self, sandbox: LocalSandbox):
        self.sandbox = sandbox

    def git_status(self) -> dict:
        """Get git status."""
        result = self.sandbox.execute("git status --porcelain")
        files = []
        # Don't use strip() as it removes leading spaces important for git status
        for line in result.output.split("\n"):
            line = line.rstrip()  # Only remove trailing whitespace
            if line and len(line) >= 3:
                status = line[:2].strip()
                filename = line[3:].strip()
                files.append({"status": status, "file": filename})
        return {
            "files": files,
            "clean": len(files) == 0,
            "output": result.output,
        }

    def git_diff(self, staged: bool = False) -> str:
        """Get git diff."""
        cmd = "git diff --cached" if staged else "git diff"
        result = self.sandbox.execute(cmd)
        return result.output

    def git_log(self, n: int = 10) -> list[dict]:
        """Get recent commits."""
        cmd = f'git log -{n} --pretty=format:"%h|%s|%an|%ai"'
        result = self.sandbox.execute(cmd)
        commits = []
        for line in result.output.strip().split("\n"):
            if "|" in line:
                parts = line.split("|", 3)
                commits.append(
                    {
                        "hash": parts[0],
                        "message": parts[1],
                        "author": parts[2],
                        "date": parts[3] if len(parts) > 3 else "",
                    }
                )
        return commits

    def git_branch(self) -> dict:
        """Get branch information."""
        result = self.sandbox.execute("git branch -vv")
        branches = []
        current = None
        for line in result.output.strip().split("\n"):
            if line.startswith("*"):
                current = line[2:].split()[0]
                branches.append({"name": current, "current": True})
            elif line.strip():
                branches.append({"name": line.strip().split()[0], "current": False})
        return {"current": current, "branches": branches}


# ============================================================================
# Search Tools
# ============================================================================


class SearchTools:
    """Code search tools."""

    def __init__(self, sandbox: LocalSandbox):
        self.sandbox = sandbox

    def grep(
        self,
        pattern: str,
        path: str = ".",
        include: Optional[str] = None,
        exclude: Optional[str] = None,
    ) -> list[dict]:
        """Search files with grep."""
        cmd = f"grep -rn"
        if include:
            cmd += f' --include="{include}"'
        if exclude:
            cmd += f' --exclude="{exclude}"'
        cmd += f' "{pattern}" {path}'

        result = self.sandbox.execute(cmd)
        matches = []
        for line in result.output.strip().split("\n"):
            if ":" in line:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append(
                        {
                            "file": parts[0],
                            "line": int(parts[1]) if parts[1].isdigit() else 0,
                            "content": parts[2],
                        }
                    )
        return matches

    def find_files(
        self,
        pattern: str = "*",
        path: str = ".",
        type_: Optional[str] = None,
    ) -> list[str]:
        """Find files matching pattern."""
        cmd = f'find {path} -name "{pattern}"'
        if type_ == "file":
            cmd += " -type f"
        elif type_ == "dir":
            cmd += " -type d"

        result = self.sandbox.execute(cmd)
        return [f.strip() for f in result.output.strip().split("\n") if f.strip()]


# ============================================================================
# Test Runner Tools
# ============================================================================


class TestTools:
    """Test runner integration."""

    def __init__(self, sandbox: LocalSandbox):
        self.sandbox = sandbox

    def run_tests(
        self,
        runner: str = "pytest",
        path: str = ".",
        verbose: bool = False,
    ) -> dict:
        """Run tests using specified runner."""
        if runner == "pytest":
            cmd = f"pytest {path}"
            if verbose:
                cmd += " -v"
        elif runner == "jest":
            cmd = f"jest {path}"
            if verbose:
                cmd += " --verbose"
        else:
            return {"error": f"Unknown test runner: {runner}"}

        result = self.sandbox.execute(cmd)
        return {
            "output": result.output,
            "exit_code": result.exit_code,
            "success": result.exit_code == 0,
        }

    def run_test_file(self, file_path: str) -> dict:
        """Run a specific test file."""
        return self.run_tests(path=file_path)


# ============================================================================
# Linter Tools
# ============================================================================


class LintTools:
    """Linter integration."""

    def __init__(self, sandbox: LocalSandbox):
        self.sandbox = sandbox

    def lint(
        self,
        linter: str,
        path: str = ".",
        fix: bool = False,
    ) -> dict:
        """Run linter on code."""
        if linter == "ruff":
            cmd = f"ruff check {path}"
            if fix:
                cmd += " --fix"
        elif linter == "eslint":
            cmd = f"eslint {path}"
            if fix:
                cmd += " --fix"
        elif linter == "mypy":
            cmd = f"mypy {path}"
        else:
            return {"error": f"Unknown linter: {linter}"}

        result = self.sandbox.execute(cmd)
        return {
            "output": result.output,
            "exit_code": result.exit_code,
            "issues": self._parse_lint_output(linter, result.output),
        }

    def _parse_lint_output(self, linter: str, output: str) -> list[dict]:
        """Parse linter output into structured issues."""
        issues = []
        # Basic parsing - can be enhanced per linter
        for line in output.strip().split("\n"):
            if ":" in line and not line.startswith(" "):
                parts = line.split(":", 3)
                if len(parts) >= 3:
                    issues.append(
                        {
                            "file": parts[0],
                            "line": parts[1] if parts[1].isdigit() else 0,
                            "message": parts[-1].strip(),
                        }
                    )
        return issues


# ============================================================================
# Tool Registration
# ============================================================================


def register_enhanced_tools(registry: ToolRegistry, sandbox: LocalSandbox) -> None:
    """Register all enhanced tools with the registry."""
    git = GitTools(sandbox)
    search = SearchTools(sandbox)
    tests = TestTools(sandbox)
    lint = LintTools(sandbox)

    # Git tools
    registry.register(
        Tool(
            name="git_status",
            category=ToolCategory.GIT,
            description="Get git repository status",
            func=git.git_status,
        )
    )
    registry.register(
        Tool(
            name="git_diff",
            category=ToolCategory.GIT,
            description="Show git diff",
            func=git.git_diff,
        )
    )
    registry.register(
        Tool(
            name="git_log",
            category=ToolCategory.GIT,
            description="Show recent git commits",
            func=git.git_log,
        )
    )
    registry.register(
        Tool(
            name="git_branch",
            category=ToolCategory.GIT,
            description="List git branches",
            func=git.git_branch,
        )
    )

    # Search tools
    registry.register(
        Tool(
            name="grep",
            category=ToolCategory.SEARCH,
            description="Search files with grep pattern",
            func=search.grep,
        )
    )
    registry.register(
        Tool(
            name="find_files",
            category=ToolCategory.SEARCH,
            description="Find files matching pattern",
            func=search.find_files,
        )
    )

    # Test tools
    registry.register(
        Tool(
            name="run_tests",
            category=ToolCategory.TEST,
            description="Run tests with specified runner",
            func=tests.run_tests,
        )
    )

    # Lint tools
    registry.register(
        Tool(
            name="lint",
            category=ToolCategory.LINT,
            description="Run linter on code",
            func=lint.lint,
        )
    )
