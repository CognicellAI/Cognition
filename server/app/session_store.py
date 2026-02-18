"""SQLite session storage.

Stores session metadata in the same SQLite database as LangGraph checkpoints.
Replaces the old JSON-based LocalSessionStore.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
import structlog

from server.app.models import Session, SessionConfig, SessionStatus

logger = structlog.get_logger(__name__)


class SqliteSessionStore:
    """SQLite-based session storage per workspace."""

    def __init__(self, workspace_path: str, db_path: str = ".cognition/state.db"):
        """Initialize store for a workspace.

        Args:
            workspace_path: Absolute path to the workspace directory.
            db_path: Path to the database file relative to workspace.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.db_path = self.workspace_path / db_path
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Ensure the storage directory exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _init_db(self) -> None:
        """Initialize the database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workspace_path TEXT NOT NULL,
                    title TEXT,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def create_session(
        self,
        session_id: str,
        thread_id: str,
        config: SessionConfig,
        title: Optional[str] = None,
        scopes: Optional[dict[str, str]] = None,
    ) -> Session:
        """Create a new session."""
        await self._init_db()
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
            scopes=scopes or {},
        )

        config_json = json.dumps(
            {
                "provider": config.provider,
                "model": config.model,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "system_prompt": config.system_prompt,
            }
        )

        scopes_json = json.dumps(session.scopes)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (
                    id, workspace_path, title, thread_id, status, 
                    config, scopes, message_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.workspace_path,
                    session.title,
                    session.thread_id,
                    session.status.value,
                    config_json,
                    scopes_json,
                    session.message_count,
                    session.created_at,
                    session.updated_at,
                ),
            )
            await db.commit()

        logger.info(
            "Session created (SQLite)",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    config_data = json.loads(row["config"])
                    scopes_data = json.loads(row["scopes"]) if row["scopes"] else {}
                    config = SessionConfig(
                        provider=config_data.get("provider"),
                        model=config_data.get("model"),
                        temperature=config_data.get("temperature"),
                        max_tokens=config_data.get("max_tokens"),
                        system_prompt=config_data.get("system_prompt"),
                    )
                    return Session(
                        id=row["id"],
                        workspace_path=row["workspace_path"],
                        title=row["title"],
                        thread_id=row["thread_id"],
                        status=SessionStatus(row["status"]),
                        config=config,
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        message_count=row["message_count"],
                        scopes=scopes_data,
                    )
        return None

    async def list_sessions(self, filter_scopes: Optional[dict[str, str]] = None) -> list[Session]:
        """List all sessions for this workspace.

        Args:
            filter_scopes: If provided, only return sessions that match all
                scope key-value pairs in this dictionary.
        """
        await self._init_db()
        sessions = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions ORDER BY updated_at DESC") as cursor:
                async for row in cursor:
                    config_data = json.loads(row["config"])
                    scopes_data = json.loads(row["scopes"]) if row["scopes"] else {}

                    # If filter_scopes is provided, only include sessions that match
                    if filter_scopes:
                        matches = all(
                            scopes_data.get(key) == value for key, value in filter_scopes.items()
                        )
                        if not matches:
                            continue

                    config = SessionConfig(
                        provider=config_data.get("provider"),
                        model=config_data.get("model"),
                        temperature=config_data.get("temperature"),
                        max_tokens=config_data.get("max_tokens"),
                        system_prompt=config_data.get("system_prompt"),
                    )
                    sessions.append(
                        Session(
                            id=row["id"],
                            workspace_path=row["workspace_path"],
                            title=row["title"],
                            thread_id=row["thread_id"],
                            status=SessionStatus(row["status"]),
                            config=config,
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                            message_count=row["message_count"],
                            scopes=scopes_data,
                        )
                    )
        return sessions

    async def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
        """Update a session."""
        session = await self.get_session(session_id)
        if not session:
            return None

        updates = []
        params = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)
            session.title = title

        if config is not None:
            # Merge existing config with new values
            existing_config = session.config
            new_config = SessionConfig(
                provider=config.provider or existing_config.provider,
                model=config.model or existing_config.model,
                temperature=config.temperature
                if config.temperature is not None
                else existing_config.temperature,
                max_tokens=config.max_tokens
                if config.max_tokens is not None
                else existing_config.max_tokens,
                system_prompt=config.system_prompt
                if config.system_prompt is not None
                else existing_config.system_prompt,
            )

            config_json = json.dumps(
                {
                    "provider": new_config.provider,
                    "model": new_config.model,
                    "temperature": new_config.temperature,
                    "max_tokens": new_config.max_tokens,
                    "system_prompt": new_config.system_prompt,
                }
            )
            updates.append("config = ?")
            params.append(config_json)
            session.config = new_config

        if not updates:
            return session

        updates.append("updated_at = ?")
        now = datetime.utcnow().isoformat()
        params.append(now)
        session.updated_at = now

        params.append(session_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()

        return session

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE sessions SET message_count = ?, updated_at = ? WHERE id = ?",
                (count, now, session_id),
            )
            await db.commit()

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()
            if cursor.rowcount > 0:
                logger.info(
                    "Session deleted (SQLite)",
                    session_id=session_id,
                    workspace=str(self.workspace_path),
                )
                return True
        return False


# Backward compatibility alias
LocalSessionStore = SqliteSessionStore


# Global cache of stores per workspace path
_store_cache: dict[str, SqliteSessionStore] = {}


def get_session_store(workspace_path: str) -> SqliteSessionStore:
    """Get or create a session store for a workspace.

    Args:
        workspace_path: Absolute path to the workspace.

    Returns:
        SqliteSessionStore for the workspace.
    """
    resolved = str(Path(workspace_path).resolve())

    if resolved not in _store_cache:
        _store_cache[resolved] = SqliteSessionStore(resolved)

    return _store_cache[resolved]
