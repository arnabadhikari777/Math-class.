# -*- coding: utf-8 -*-
"""Database package: connection helpers, schema creation, migrations."""
from backend.db.connection import connect, db_path, escape_like, get_db

__all__ = ["connect", "db_path", "escape_like", "get_db"]
