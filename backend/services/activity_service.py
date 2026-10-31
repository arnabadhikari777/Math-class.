# -*- coding: utf-8 -*-
"""Activity log writer (teacher / student actions)."""

from datetime import datetime

from backend.db.connection import get_db


def log_activity(actor, action, detail=""):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO activity_log (actor, action, detail, created_at) VALUES (?,?,?,?)",
                (actor, action, detail, datetime.now().isoformat())
            )
            conn.commit()
    except Exception:
        pass
