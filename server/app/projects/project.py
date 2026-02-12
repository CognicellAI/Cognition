"""Project data models and types."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# Project prefix validation: lowercase, start with letter, alphanumeric + hyphens
PROJECT_PREFIX_REGEX = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def generate_project_id(user_prefix: str) -> str:
    """Generate a unique project ID from user prefix.

    Format: {prefix}-{short_uuid}
    Example: my-api-a7b3c2d1

    Args:
        user_prefix: User-provided prefix (validated)

    Returns:
        Full project ID with UUID suffix
    """
    short_uuid = str(uuid.uuid4())[:8]
    return f"{user_prefix}-{short_uuid}"


def validate_project_prefix(prefix: str) -> None:
    """Validate project prefix follows rules.

    Rules:
    - Lowercase only
    - Start with letter
    - Alphanumeric + hyphens
    - Max 32 characters

    Args:
        prefix: User-provided prefix

    Raises:
        ValueError: If prefix is invalid
    """
    if not PROJECT_PREFIX_REGEX.match(prefix):
        raise ValueError(
            f"Invalid project prefix '{prefix}'. "
            "Must be lowercase, start with letter, contain only letters/numbers/hyphens, "
            "and be at most 32 characters."
        )


@dataclass
class ProjectConfig:
    """Project configuration settings."""

    network_mode: str = "OFF"
    repo_url: str | None = None
    agent_model: str = "claude-haiku-4.5"
    container_image: str = "opencode-agent:py"
    agent_backend_routes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "network_mode": self.network_mode,
            "repo_url": self.repo_url,
            "agent_model": self.agent_model,
            "container_image": self.container_image,
            "agent_backend_routes": self.agent_backend_routes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        """Create from dictionary."""
        return cls(
            network_mode=data.get("network_mode", "OFF"),
            repo_url=data.get("repo_url"),
            agent_model=data.get("agent_model", "claude-haiku-4.5"),
            container_image=data.get("container_image", "opencode-agent:py"),
            agent_backend_routes=data.get("agent_backend_routes"),
        )


@dataclass
class SessionRecord:
    """Record of a completed or active session."""

    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    messages: int = 0
    tasks_completed: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """Check if session is currently active."""
        return self.ended_at is None

    @property
    def duration_seconds(self) -> int:
        """Get session duration in seconds."""
        if self.ended_at is None:
            return int((datetime.utcnow() - self.started_at).total_seconds())
        return int((self.ended_at - self.started_at).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "messages": self.messages,
            "tasks_completed": self.tasks_completed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=datetime.fromisoformat(data["ended_at"]) if data.get("ended_at") else None,
            messages=data.get("messages", 0),
            tasks_completed=data.get("tasks_completed", []),
        )


@dataclass
class ProjectStatistics:
    """Project usage statistics."""

    total_sessions: int = 0
    total_messages: int = 0
    files_modified: int = 0
    tests_run: int = 0
    total_duration_seconds: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_sessions": self.total_sessions,
            "total_messages": self.total_messages,
            "files_modified": self.files_modified,
            "tests_run": self.tests_run,
            "total_duration_seconds": self.total_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectStatistics:
        """Create from dictionary."""
        return cls(
            total_sessions=data.get("total_sessions", 0),
            total_messages=data.get("total_messages", 0),
            files_modified=data.get("files_modified", 0),
            tests_run=data.get("tests_run", 0),
            total_duration_seconds=data.get("total_duration_seconds", 0),
        )


@dataclass
class Project:
    """Represents a persistent project with multiple sessions."""

    project_id: str
    user_prefix: str
    created_at: datetime
    last_accessed: datetime
    config: ProjectConfig
    sessions: list[SessionRecord] = field(default_factory=list)
    statistics: ProjectStatistics = field(default_factory=ProjectStatistics)
    tags: list[str] = field(default_factory=list)
    description: str = ""
    pinned: bool = False
    cleanup_after_days: int = 30

    @property
    def days_until_cleanup(self) -> int | None:
        """Calculate days until auto-cleanup (None if pinned)."""
        if self.pinned:
            return None
        cleanup_date = self.last_accessed + timedelta(days=self.cleanup_after_days)
        days_remaining = (cleanup_date - datetime.utcnow()).days
        return max(0, days_remaining)

    @property
    def is_pending_deletion(self) -> bool:
        """Check if project is pending deletion."""
        days = self.days_until_cleanup
        return days is not None and days <= 0

    @property
    def active_session(self) -> SessionRecord | None:
        """Get currently active session if any."""
        for session in self.sessions:
            if session.is_active:
                return session
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "project_id": self.project_id,
            "user_prefix": self.user_prefix,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "config": self.config.to_dict(),
            "sessions": [s.to_dict() for s in self.sessions],
            "statistics": self.statistics.to_dict(),
            "tags": self.tags,
            "description": self.description,
            "pinned": self.pinned,
            "cleanup_after_days": self.cleanup_after_days,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        """Create from dictionary."""
        return cls(
            project_id=data["project_id"],
            user_prefix=data["user_prefix"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_accessed=datetime.fromisoformat(data["last_accessed"]),
            config=ProjectConfig.from_dict(data.get("config", {})),
            sessions=[SessionRecord.from_dict(s) for s in data.get("sessions", [])],
            statistics=ProjectStatistics.from_dict(data.get("statistics", {})),
            tags=data.get("tags", []),
            description=data.get("description", ""),
            pinned=data.get("pinned", False),
            cleanup_after_days=data.get("cleanup_after_days", 30),
        )
