from __future__ import annotations

from server.app.storage.postgres import _normalize_sqlalchemy_async_dsn


def test_normalize_sqlalchemy_async_dsn_converts_plain_postgres_uri() -> None:
    uri = "postgresql://user:pass@host:5432/db"

    assert _normalize_sqlalchemy_async_dsn(uri) == "postgresql+asyncpg://user:pass@host:5432/db"


def test_normalize_sqlalchemy_async_dsn_preserves_asyncpg_uri() -> None:
    uri = "postgresql+asyncpg://user:pass@host:5432/db"

    assert _normalize_sqlalchemy_async_dsn(uri) == uri
