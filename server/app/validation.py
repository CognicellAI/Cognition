"""Validation utilities for Cognition.

Provides input validation and sanitization.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from server.app.exceptions import ValidationError


# Patterns for dangerous paths
DANGEROUS_PATHS = [
    r"\.\.",  # Parent directory references
    r"^~",  # Home directory
    r"^/",  # Absolute paths (handled separately)
]

# Maximum allowed values
MAX_PROJECT_NAME_LENGTH = 100
MAX_MESSAGE_LENGTH = 10000
MAX_FILE_PATH_LENGTH = 500


def validate_project_name(name: str) -> str:
    """Validate project name.

    Args:
        name: Project name to validate

    Returns:
        Sanitized project name

    Raises:
        ValidationError: If name is invalid
    """
    if not name:
        raise ValidationError("project_name", "Project name cannot be empty")

    if len(name) > MAX_PROJECT_NAME_LENGTH:
        raise ValidationError(
            "project_name", f"Project name too long (max {MAX_PROJECT_NAME_LENGTH} chars)"
        )

    # Allow alphanumeric, hyphens, underscores, spaces
    if not re.match(r"^[\w\s-]+$", name):
        raise ValidationError(
            "project_name",
            "Project name can only contain letters, numbers, spaces, hyphens, and underscores",
        )

    # Sanitize: remove leading/trailing whitespace and special chars
    sanitized = name.strip().replace(" ", "_")

    return sanitized


def validate_project_path(path: str) -> Path:
    """Validate and sanitize project path.

    Args:
        path: Project path to validate

    Returns:
        Sanitized Path object

    Raises:
        ValidationError: If path is invalid or dangerous
    """
    if not path:
        raise ValidationError("project_path", "Path cannot be empty")

    if len(path) > MAX_FILE_PATH_LENGTH:
        raise ValidationError("project_path", f"Path too long (max {MAX_FILE_PATH_LENGTH} chars)")

    # Check for dangerous patterns
    for pattern in DANGEROUS_PATHS:
        if re.search(pattern, path):
            raise ValidationError("project_path", f"Path contains dangerous characters: {pattern}")

    # Convert to Path and ensure it's not absolute
    p = Path(path)
    if p.is_absolute():
        raise ValidationError("project_path", "Absolute paths are not allowed")

    return p


def validate_message_content(content: str) -> str:
    """Validate user message content.

    Args:
        content: Message content to validate

    Returns:
        Sanitized content

    Raises:
        ValidationError: If content is invalid
    """
    if not content:
        raise ValidationError("content", "Message content cannot be empty")

    if len(content) > MAX_MESSAGE_LENGTH:
        raise ValidationError("content", f"Message too long (max {MAX_MESSAGE_LENGTH} chars)")

    # Basic sanitization: strip control chars except newlines
    sanitized = "".join(char for char in content if char == "\n" or ord(char) >= 32)

    return sanitized.strip()


def validate_session_id(session_id: str) -> str:
    """Validate session ID format.

    Args:
        session_id: Session ID to validate

    Returns:
        Validated session ID

    Raises:
        ValidationError: If session ID is invalid
    """
    if not session_id:
        raise ValidationError("session_id", "Session ID cannot be empty")

    # UUID format validation (accepts both with and without hyphens)
    if not re.match(r"^[0-9a-fA-F-]{32,36}$", session_id):
        raise ValidationError("session_id", "Invalid session ID format")

    return session_id.lower()


def sanitize_file_path(path: str, base_dir: Path) -> Path:
    """Sanitize file path within base directory.

    Args:
        path: Relative file path
        base_dir: Base directory for the path

    Returns:
        Resolved, safe path

    Raises:
        ValidationError: If path escapes base directory
    """
    # Resolve the full path
    full_path = (base_dir / path).resolve()

    # Ensure path is within base directory
    try:
        full_path.relative_to(base_dir.resolve())
    except ValueError:
        raise ValidationError("file_path", "Path escapes allowed directory")

    return full_path


def redact_secrets(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from dictionary.

    Args:
        data: Dictionary potentially containing secrets

    Returns:
        Copy with secrets redacted
    """
    secret_keys = [
        "api_key",
        "apikey",
        "api_key",
        "secret",
        "secret_key",
        "private_key",
        "password",
        "token",
        "auth",
    ]

    result = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(secret in key_lower for secret in secret_keys):
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = redact_secrets(value)
        elif isinstance(value, list):
            result[key] = [
                redact_secrets(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            result[key] = value

    return result
