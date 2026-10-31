# -*- coding: utf-8 -*-
"""SQLite connection helpers.

One database file for the whole project: ``<project>/database.db``. No ORM.
Tables are created in ``schema.py``; later changes live in ``migrations.py``.
"""
import sqlite3

from backend.config import DB_PATH


def db_path(app=None):
    """Path of the database file (the ``app`` argument is kept for old callers)."""
    return DB_PATH


def connect(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Open a connection (use ``with get_db() as conn``)."""
    return connect(DB_PATH)


def escape_like(text):
    """Escape %, _ and \\ so user input is matched literally in LIKE ... ESCAPE '\\'."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
