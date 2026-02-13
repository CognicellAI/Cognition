"""Local session storage.

Stores sessions in .cognition/sessions.json within each workspace.
Each workspace is isolated - sessions don't leak between directories.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from server.app.models import Session, SessionConfig, SessionStatus

logger = structlog.get_logger(__name__)


class LocalSessionStore:
    """File-based session storage per workspace.

    Each workspace has its own .cognition/sessions.json file.
    Sessions are isolated per workspace directory.
    """

    COGNITION_DIR = ".cognition"
    SESSIONS_FILE = "sessions.json"

    def __init__(self, workspace_path: str):
        """Initialize store for a workspace.

        Args:
            workspace_path: Absolute path to the workspace directory.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.storage_path = self.workspace_path / self.COGNITION_DIR / self.SESSIONS_FILE
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Ensure the storage directory and file exist."""
        config_dir = self.workspace_path / self.COGNITION_DIR
        config_dir.mkdir(parents=True, exist_ok=True)

        if not self.storage_path.exists():
            self.storage_path.write_text("{}")

    def _load_all(self) -> dict[str, dict]:
        """Load all sessions from file.

        Returns:
            Dictionary mapping session_id to session data.
        """
        try:
            content = self.storage_path.read_text()
            return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError):
            logger.warning("Failed to load sessions", path=str(self.storage_path))
            return {}

    def _save_all(self, sessions: dict[str, dict]) -> None:
        """Save all sessions to file.

        Args:
            sessions: Dictionary mapping session_id to session data.
        """
        self._ensure_storage()
        self.storage_path.write_text(json.dumps(sessions, indent=2, default=str))

    def create_session(
        self,
        session_id: str,
        thread_id: str,
        config: SessionConfig,
        title: Optional[str] = None,
    ) -> Session:
        """Create a new session.

        Args:
            session_id: Unique session identifier.
            thread_id: Thread identifier for checkpointing.
            config: Session configuration.
            title: Optional session title.

        Returns:
            Created session.
        """
        now = datetime.utcnow().isoformat()

        session = Session(
            id=session_id,
            workspace_path=str(self.workspace_path),
            title=title,
            thread_id=thread_id,
            status=SessionStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
            message_count=0,
        )

        sessions = self._load_all()
        sessions[session_id] = session.to_dict()
        self._save_all(sessions)

        logger.info(
            "Session created",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID.

        Args:
            session_id: Session identifier.

        Returns:
            Session if found, None otherwise.
        """
        sessions = self._load_all()
        data = sessions.get(session_id)

        if data:
            return Session.from_dict(data)

        return None

    def list_sessions(self) -> list[Session]:
        """List all sessions for this workspace.

        Returns:
            List of sessions, sorted by updated_at descending.
        """
        sessions = self._load_all()

        results = [Session.from_dict(data) for data in sessions.values()]

        # Sort by updated_at descending (most recent first)
        results.sort(key=lambda s: s.updated_at, reverse=True)

        return results

    def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
        """Update a session.

        Args:
            session_id: Session to update.
            title: New title (if provided).
            config: New config (if provided).

        Returns:
            Updated session if found, None otherwise.
        """
        sessions = self._load_all()
        data = sessions.get(session_id)

        if not data:
            return None

        # Update fields
        if title is not None:
            data["title"] = title

        if config is not None:
            data["config"] = {
                "provider": config.provider,
                "model": config.model,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "system_prompt": config.system_prompt,
            }

        data["updated_at"] = datetime.utcnow().isoformat()

        sessions[session_id] = data
        self._save_all(sessions)

        return Session.from_dict(data)

    def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session.

        Args:
            session_id: Session to update.
            count: New message count.
        """
        sessions = self._load_all()
        data = sessions.get(session_id)

        if data:
            data["message_count"] = count
            data["updated_at"] = datetime.utcnow().isoformat()
            sessions[session_id] = data
            self._save_all(sessions)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: Session to delete.

        Returns:
            True if deleted, False if not found.
        """
        sessions = self._load_all()

        if session_id in sessions:
            del sessions[session_id]
            self._save_all(sessions)

            logger.info(
                "Session deleted",
                session_id=session_id,
                workspace=str(self.workspace_path),
            )
            return True

        return False

    def get_most_recent_session(self) -> Optional[Session]:
        """Get the most recently updated session.

        Returns:
            Most recent session, or None if no sessions exist.
        """
        sessions = self.list_sessions()
        return sessions[0] if sessions else None


# Global cache of stores per workspace path
_store_cache: dict[str, LocalSessionStore] = {}


def get_session_store(workspace_path: str) -> LocalSessionStore:
    """Get or create a session store for a workspace.

    Args:
        workspace_path: Absolute path to the workspace.

    Returns:
        LocalSessionStore for the workspace.
    """
    resolved = str(Path(workspace_path).resolve())

    if resolved not in _store_cache:
        _store_cache[resolved] = LocalSessionStore(resolved)

    return _store_cache[resolved]
