"""Tests for safety validation."""

import pytest

from server.app.exceptions import PathValidationError, ToolValidationError
from server.app.tools.safety import SafetyValidator


class MockSessionManager:
    """Mock session manager for testing."""

    def __init__(self) -> None:
        self.workspace_manager = MockWorkspaceManager()


class MockWorkspaceManager:
    """Mock workspace manager for testing."""

    def validate_path_in_workspace(self, session_id: str, path: str) -> None:
        """Mock validation - always succeeds."""
        pass


class TestArgvOnlyValidation:
    """Test argv-only command validation."""

    def test_valid_argv(self) -> None:
        """Test valid argv command."""
        validator = SafetyValidator(MockSessionManager())
        validator.validate_argv_only(["pytest", "-q"])  # Should not raise

    def test_shell_metacharacters(self) -> None:
        """Test detection of shell metacharacters."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(ToolValidationError, match="Shell metacharacters"):
            validator.validate_argv_only(["bash", "-c", "echo hello; rm -rf /"])

    def test_pipe_character(self) -> None:
        """Test detection of pipe character."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(ToolValidationError, match="Shell metacharacters"):
            validator.validate_argv_only(["cat", "file", "|", "grep", "test"])

    def test_not_a_list(self) -> None:
        """Test that string commands are rejected."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(ToolValidationError, match="must be a list"):
            validator.validate_argv_only("pytest -q")  # type: ignore[arg-type]


class TestPathValidation:
    """Test path validation."""

    def test_valid_path(self) -> None:
        """Test valid path."""
        validator = SafetyValidator(MockSessionManager())
        validator.validate_path("session-123", "test.py")  # Should not raise

    def test_path_traversal(self) -> None:
        """Test path traversal detection."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(PathValidationError, match="Path traversal"):
            validator.validate_path("session-123", "../../../etc/passwd")

    def test_empty_path(self) -> None:
        """Test empty path rejection."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(PathValidationError, match="cannot be empty"):
            validator.validate_path("session-123", "")

    def test_absolute_path_outside_workspace(self) -> None:
        """Test absolute path outside workspace."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(PathValidationError, match="outside workspace"):
            validator.validate_path("session-123", "/etc/passwd")


class TestDiffValidation:
    """Test diff validation."""

    def test_valid_diff(self) -> None:
        """Test valid unified diff."""
        validator = SafetyValidator(MockSessionManager())
        diff = """--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-old
+new
"""
        validator.validate_diff(diff)  # Should not raise

    def test_empty_diff(self) -> None:
        """Test empty diff rejection."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(ToolValidationError, match="cannot be empty"):
            validator.validate_diff("")

    def test_invalid_format(self) -> None:
        """Test invalid diff format."""
        validator = SafetyValidator(MockSessionManager())
        with pytest.raises(ToolValidationError, match="Invalid diff format"):
            validator.validate_diff("This is not a diff")
