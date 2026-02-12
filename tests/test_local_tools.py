"""Tests for local tools (GitTools, TestTools)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.local_tools import GitTools, TestTools


class TestGitTools:
    """Test GitTools functionality."""

    def test_git_status_clean_repo(self):
        """Test git_status on a clean repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            tools = GitTools(working_dir=tmpdir)
            result = tools.git_status()

            assert "No changes" in result or result == ""

    def test_git_status_with_changes(self):
        """Test git_status with uncommitted changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Create a file
            Path(tmpdir).joinpath("test.txt").write_text("content")

            tools = GitTools(working_dir=tmpdir)
            result = tools.git_status()

            assert "test.txt" in result or "??" in result

    def test_git_diff_no_changes(self):
        """Test git_diff with no changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            tools = GitTools(working_dir=tmpdir)
            result = tools.git_diff()

            assert "No differences" in result or result == ""

    def test_git_diff_with_changes(self):
        """Test git_diff with changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Create and commit a file
            Path(tmpdir).joinpath("test.txt").write_text("original")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Modify the file
            Path(tmpdir).joinpath("test.txt").write_text("modified")

            tools = GitTools(working_dir=tmpdir)
            result = tools.git_diff()

            assert "original" in result or "modified" in result or "test.txt" in result

    def test_git_diff_staged(self):
        """Test git_diff with staged changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Create and commit a file
            Path(tmpdir).joinpath("test.txt").write_text("original")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Modify and stage the file
            Path(tmpdir).joinpath("test.txt").write_text("staged")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)

            tools = GitTools(working_dir=tmpdir)
            result = tools.git_diff(staged=True)

            assert "staged" in result or "test.txt" in result

    def test_tools_property(self):
        """Test that tools property returns LangChain Tool instances."""
        tools = GitTools()
        tool_list = tools.tools

        assert len(tool_list) == 2
        assert any(t.name == "git_status" for t in tool_list)
        assert any(t.name == "git_diff" for t in tool_list)


class TestTestTools:
    """Test TestTools functionality."""

    def test_run_tests_success(self):
        """Test running tests that pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple test file
            test_file = Path(tmpdir) / "test_sample.py"
            test_file.write_text("""
def test_passing():
    assert True

def test_another():
    assert 1 + 1 == 2
""")

            tools = TestTools(working_dir=tmpdir)
            result = tools.run_tests("pytest -v")

            assert "passed" in result or "2 passed" in result
            assert "failed" not in result.lower() or result.count("failed") == 0

    def test_run_tests_failure(self):
        """Test running tests that fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a failing test file
            test_file = Path(tmpdir) / "test_failing.py"
            test_file.write_text("""
def test_failing():
    assert False, "This test should fail"
""")

            tools = TestTools(working_dir=tmpdir)
            result = tools.run_tests("pytest -v")

            assert "failed" in result.lower()
            assert "Exit code: 1" in result or result.count("failed") > 0

    def test_run_tests_no_tests(self):
        """Test running tests when no tests exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = TestTools(working_dir=tmpdir)
            result = tools.run_tests("pytest -v")

            assert "no tests ran" in result.lower() or "collected 0" in result.lower()

    def test_run_tests_timeout(self):
        """Test that long-running tests can be handled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test that takes a bit of time
            test_file = Path(tmpdir) / "test_slow.py"
            test_file.write_text("""
import time

def test_slow():
    time.sleep(0.1)  # Short sleep for testing
    assert True
""")

            tools = TestTools(working_dir=tmpdir)
            result = tools.run_tests("pytest test_slow.py -v")

            assert "passed" in result

    def test_run_tests_custom_args(self):
        """Test running tests with custom arguments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple test files
            Path(tmpdir).joinpath("test_a.py").write_text("def test_a(): assert True")
            Path(tmpdir).joinpath("test_b.py").write_text("def test_b(): assert True")

            tools = TestTools(working_dir=tmpdir)
            result = tools.run_tests("pytest test_a.py -v")

            assert "test_a" in result
            # Should only run test_a, not test_b

    def test_tools_property(self):
        """Test that tools property returns LangChain Tool instances."""
        tools = TestTools()
        tool_list = tools.tools

        assert len(tool_list) == 1
        assert tool_list[0].name == "run_tests"


class TestToolIntegration:
    """Integration tests for tools in a realistic scenario."""

    def test_git_workflow(self):
        """Test a complete git workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            git_tools = GitTools(working_dir=tmpdir)

            # Initial status should be clean
            status = git_tools.git_status()
            assert "No changes" in status or status == ""

            # Create a file
            Path(tmpdir).joinpath("main.py").write_text("print('hello')")

            # Status should show untracked file
            status = git_tools.git_status()
            assert "main.py" in status

            # Diff should be empty (file not staged)
            diff = git_tools.git_diff()
            assert "No differences" in diff or diff == ""

            # Stage the file
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)

            # Staged diff should show the file
            diff_staged = git_tools.git_diff(staged=True)
            assert "main.py" in diff_staged

            # Commit
            subprocess.run(
                ["git", "commit", "-m", "Add main.py"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Status should be clean again
            status = git_tools.git_status()
            assert "No changes" in status or status == ""

    def test_development_workflow(self):
        """Test a complete development workflow with tests and git."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "dev@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Developer"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            git_tools = GitTools(working_dir=tmpdir)
            test_tools = TestTools(working_dir=tmpdir)

            # Create a Python module with tests
            Path(tmpdir).joinpath("calculator.py").write_text("""
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
""")

            Path(tmpdir).joinpath("test_calculator.py").write_text("""
from calculator import add, subtract

def test_add():
    assert add(2, 3) == 5

def test_subtract():
    assert subtract(5, 3) == 2
""")

            # Run tests
            test_result = test_tools.run_tests("pytest -v")
            assert "passed" in test_result

            # Check git status
            status = git_tools.git_status()
            assert "calculator.py" in status
            assert "test_calculator.py" in status

            # Stage and commit
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Add calculator with tests"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )

            # Status should be clean
            status = git_tools.git_status()
            assert "No changes" in status or status == ""
