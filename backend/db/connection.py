# -*- coding: utf-8 -*-
"""SQLite connection helpers.

One database file for the whole project: ``<project>/database.db``. No ORM.
Tables are created in ``schema.py``; later changes live in ``migrations.py``.
"""
import logging
import os
import sqlite3

from backend.config import DB_PATH

log = logging.getLogger(__name__)

# WAL lets readers and the one writer work at the same time. It is OFF by default because some
# network file systems (PythonAnywhere included, depending on the account) do not support it well.
# Turn it on with SQLITE_WAL=1 only after reading docs/DEPLOY.md.
USE_WAL = os.environ.get("SQLITE_WAL", "0").strip().lower() in ("1", "true", "yes")
BUSY_TIMEOUT_MS = 15000


class Connection(sqlite3.Connection):
    """sqlite3 connection whose ``with`` block ALSO closes it.

    The standard ``with conn:`` only commits/rolls back and leaves the file handle open until the
    garbage collector runs; under many requests that causes "database is locked" and leaked handles.
    Everything fetched with fetchone()/fetchall() inside the block stays usable afterwards.
    """

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            try:
                self.close()
            except sqlite3.Error:  # pragma: no cover - closing twice is harmless
                pass


def db_path(app=None):
    """Path of the database file (the ``app`` argument is kept for old callers)."""
    return DB_PATH


def connect(path):
    conn = sqlite3.connect(path, timeout=15, factory=Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = %d" % BUSY_TIMEOUT_MS)
    if USE_WAL:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error as exc:  # keep working in the old mode
            log.warning("SQLite WAL could not be enabled: %s", exc)
    return conn


def get_db():
    """Open a connection (use ``with get_db() as conn``)."""
    return connect(DB_PATH)


def escape_like(text):
    """Escape %, _ and \\ so user input is matched literally in LIKE ... ESCAPE '\\'."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
