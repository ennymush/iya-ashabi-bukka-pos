"""Database bootstrap helpers for SQLite."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.utils.logging_config import get_logger

log = get_logger("database")


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover - driver hook
    """Apply SQLite durability / concurrency settings on every new connection."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        try:
            # WAL keeps the UI responsive while a sale is being written.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
        finally:
            cursor.close()


def integrity_check(db_path) -> tuple[bool, str]:
    """Verify the database is not corrupt. Used on startup."""
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            result = (row[0] if row else "unknown").lower()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, str(exc)
    return (result == "ok"), result
