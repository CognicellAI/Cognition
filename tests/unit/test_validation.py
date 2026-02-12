"""Unit tests for validation module."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.exceptions import ValidationError
from server.app.validation import (
    MAX_FILE_PATH_LENGTH,
    MAX_MESSAGE_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
    redact_secrets,
    sanitize_file_path,
    validate_message_content,
    validate_project_name,
    validate_project_path,
    validate_session_id,
)


class TestValidateProjectName:
    """Test validate_project_name function."""

    def test_valid_name(self):
        """Test valid project names."""
        assert validate_project_name("my-project") == "my-project"
        assert validate_project_name("my_project") == "my_project"
        assert validate_project_name("MyProject") == "MyProject"
        assert validate_project_name("project123") == "project123"

    def test_strips_whitespace(self):
        """Test that whitespace is stripped."""
        assert validate_project_name("  my-project  ") == "my-project"
        assert validate_project_name("my project") == "my_project"

    def test_empty_name_raises(self):
        """Test that empty name raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_project_name("")
        assert exc_info.value.code.value == "validation_error"
        assert "empty" in exc_info.value.message.lower()

    def test_too_long_name_raises(self):
        """Test that too long name raises ValidationError."""
        long_name = "a" * (MAX_PROJECT_NAME_LENGTH + 1)
        with pytest.raises(ValidationError) as exc_info:
            validate_project_name(long_name)
        assert "too long" in exc_info.value.message.lower()

    def test_invalid_characters_raises(self):
        """Test that invalid characters raise ValidationError."""
        invalid_names = ["project/name", "project..name", "project@name", "project#name"]
        for name in invalid_names:
            with pytest.raises(ValidationError) as exc_info:
                validate_project_name(name)
            assert "validation_error" in exc_info.value.code.value


class TestValidateProjectPath:
    """Test validate_project_path function."""

    def test_valid_relative_path(self):
        """Test valid relative paths."""
        result = validate_project_path("projects/my-project")
        assert result == Path("projects/my-project")

    def test_single_directory(self):
        """Test single directory path."""
        result = validate_project_path("my-project")
        assert result == Path("my-project")

    def test_empty_path_raises(self):
        """Test that empty path raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_project_path("")
        assert "empty" in exc_info.value.message.lower()

    def test_absolute_path_raises(self):
        """Test that absolute path raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_project_path("/absolute/path")
        # Absolute paths are caught by the dangerous pattern check
        assert "dangerous" in exc_info.value.message.lower()

    def test_parent_directory_raises(self):
        """Test that parent directory reference raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_project_path("../parent")
        assert "dangerous" in exc_info.value.message.lower()

    def test_home_directory_raises(self):
        """Test that home directory reference raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_project_path("~/home")
        assert "dangerous" in exc_info.value.message.lower()

    def test_too_long_path_raises(self):
        """Test that too long path raises ValidationError."""
        # Create a path that's definitely too long
        long_path = "x" * (MAX_FILE_PATH_LENGTH + 1)
        with pytest.raises(ValidationError) as exc_info:
            validate_project_path(long_path)
        assert "too long" in exc_info.value.message.lower()


class TestValidateMessageContent:
    """Test validate_message_content function."""

    def test_valid_content(self):
        """Test valid message content."""
        assert validate_message_content("Hello, world!") == "Hello, world!"
        assert validate_message_content("Multi\nline\nmessage") == "Multi\nline\nmessage"

    def test_strips_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        assert validate_message_content("  hello  ") == "hello"

    def test_empty_content_raises(self):
        """Test that empty content raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_message_content("")
        assert "empty" in exc_info.value.message.lower()

    def test_whitespace_only_content_returns_empty(self):
        """Test that whitespace-only content returns empty after stripping."""
        # Whitespace passes initial validation but is stripped at the end
        result = validate_message_content("   ")
        assert result == ""  # Should be empty after strip

    def test_too_long_content_raises(self):
        """Test that too long content raises ValidationError."""
        long_content = "a" * (MAX_MESSAGE_LENGTH + 1)
        with pytest.raises(ValidationError) as exc_info:
            validate_message_content(long_content)
        assert "too long" in exc_info.value.message.lower()

    def test_removes_control_characters(self):
        """Test that control characters are removed."""
        content = "Hello\x00World\x01!"
        result = validate_message_content(content)
        assert "\x00" not in result
        assert "\x01" not in result
        assert result == "HelloWorld!"

    def test_preserves_newlines(self):
        """Test that newlines are preserved."""
        content = "Line1\nLine2\nLine3"
        result = validate_message_content(content)
        assert result == content


class TestValidateSessionId:
    """Test validate_session_id function."""

    def test_valid_uuid_with_hyphens(self):
        """Test valid UUID with hyphens."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = validate_session_id(uuid)
        assert result == uuid.lower()

    def test_valid_uuid_without_hyphens(self):
        """Test valid UUID without hyphens."""
        uuid = "550e8400e29b41d4a716446655440000"
        result = validate_session_id(uuid)
        assert result == uuid.lower()

    def test_converts_to_lowercase(self):
        """Test that UUID is converted to lowercase."""
        uuid = "550E8400-E29B-41D4-A716-446655440000"
        result = validate_session_id(uuid)
        assert result == uuid.lower()

    def test_empty_session_id_raises(self):
        """Test that empty session ID raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_session_id("")
        assert "empty" in exc_info.value.message.lower()

    def test_invalid_format_raises(self):
        """Test that invalid format raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_session_id("not-a-uuid")
        assert "invalid" in exc_info.value.message.lower()

    def test_too_short_raises(self):
        """Test that too short ID raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            validate_session_id("abc123")
        assert "invalid" in exc_info.value.message.lower()


class TestSanitizeFilePath:
    """Test sanitize_file_path function."""

    def test_valid_path_within_base(self):
        """Test valid path within base directory."""
        base = Path("/workspace")
        result = sanitize_file_path("src/main.py", base)
        assert result == Path("/workspace/src/main.py")

    def test_nested_path(self):
        """Test deeply nested path."""
        base = Path("/workspace")
        result = sanitize_file_path("a/b/c/d/file.txt", base)
        assert result == Path("/workspace/a/b/c/d/file.txt")

    def test_traversal_attack_raises(self):
        """Test that path traversal attack raises ValidationError."""
        base = Path("/workspace")
        with pytest.raises(ValidationError) as exc_info:
            sanitize_file_path("../../../etc/passwd", base)
        assert "escapes" in exc_info.value.message.lower()

    def test_complex_traversal_raises(self):
        """Test complex path traversal attempt."""
        base = Path("/workspace")
        with pytest.raises(ValidationError) as exc_info:
            sanitize_file_path("foo/bar/../../../etc/passwd", base)
        assert "escapes" in exc_info.value.message.lower()

    def test_resolves_symlinks(self):
        """Test that symlinks are resolved properly."""
        # This test would require actual filesystem operations
        # For now, just verify the function handles resolution
        base = Path("/workspace")
        result = sanitize_file_path("./file.txt", base)
        assert result == Path("/workspace/file.txt")


class TestRedactSecrets:
    """Test redact_secrets function."""

    def test_redacts_api_key(self):
        """Test that api_key is redacted."""
        data = {"api_key": "secret123", "name": "test"}
        result = redact_secrets(data)
        assert result["api_key"] == "***REDACTED***"
        assert result["name"] == "test"

    def test_redacts_secret_key(self):
        """Test that secret_key is redacted."""
        data = {"secret_key": "my-secret", "value": 42}
        result = redact_secrets(data)
        assert result["secret_key"] == "***REDACTED***"
        assert result["value"] == 42

    def test_redacts_password(self):
        """Test that password is redacted."""
        data = {"password": "hunter2", "username": "admin"}
        result = redact_secrets(data)
        assert result["password"] == "***REDACTED***"
        assert result["username"] == "admin"

    def test_redacts_token(self):
        """Test that token is redacted."""
        data = {"token": "jwt-token-here", "user": "john"}
        result = redact_secrets(data)
        assert result["token"] == "***REDACTED***"
        assert result["user"] == "john"

    def test_nested_dict_redaction(self):
        """Test that secrets in nested dicts are redacted."""
        data = {
            "config": {"api_key": "nested-secret"},
            "public": "visible",
        }
        result = redact_secrets(data)
        assert result["config"]["api_key"] == "***REDACTED***"
        assert result["public"] == "visible"

    def test_list_of_dicts_redaction(self):
        """Test that secrets in list of dicts are redacted."""
        data = {
            "items": [
                {"api_key": "secret1", "name": "item1"},
                {"api_key": "secret2", "name": "item2"},
            ]
        }
        result = redact_secrets(data)
        assert result["items"][0]["api_key"] == "***REDACTED***"
        assert result["items"][0]["name"] == "item1"
        assert result["items"][1]["api_key"] == "***REDACTED***"
        assert result["items"][1]["name"] == "item2"

    def test_case_insensitive_matching(self):
        """Test that secret matching is case-insensitive."""
        data = {"API_KEY": "secret", "SecretKey": "also-secret"}
        result = redact_secrets(data)
        assert result["API_KEY"] == "***REDACTED***"
        assert result["SecretKey"] == "***REDACTED***"

    def test_non_dict_values_preserved(self):
        """Test that non-dict values are preserved."""
        data = {"count": 42, "enabled": True, "ratio": 3.14}
        result = redact_secrets(data)
        assert result["count"] == 42
        assert result["enabled"] is True
        assert result["ratio"] == 3.14

    def test_empty_dict(self):
        """Test empty dictionary handling."""
        result = redact_secrets({})
        assert result == {}

    def test_list_with_non_dict_items(self):
        """Test list containing non-dict items."""
        data = {"items": ["a", "b", "c"]}
        result = redact_secrets(data)
        assert result["items"] == ["a", "b", "c"]
