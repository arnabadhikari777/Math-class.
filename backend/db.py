# -*- coding: utf-8 -*-
"""Database access for the backend package.

Uses the SAME SQLite file as app.py (``<project>/database.db``). No second
database, no ORM, no schema creation here - schema changes live only in
``backend/migrations.py``.
"""
import os
import sqlite3

from flask import current_app


def db_path(app=None):
    app = app or current_app
    return app.config.get("BACKEND_DB_PATH") or os.path.join(app.root_path, "database.db")


def connect(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Open a connection for the current app (use ``with get_db() as conn``)."""
    return connect(db_path())


def escape_like(text):
    """Escape %, _ and \\ so user input is matched literally in LIKE ... ESCAPE '\\'."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
