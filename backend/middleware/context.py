# -*- coding: utf-8 -*-
"""Template context processors (class options, JEE widget, notifications)."""

from flask import session

from backend.core import app
from backend.db.connection import get_db
from backend.utils.jee import jee_sessions_state


@app.context_processor
def inject_class_options():
    """Canonical class dropdown options for all forms (single source of truth)."""
    from backend.utils.classes import class_options, DISPLAY_LABELS, CANONICAL_CLASSES, display_class
    return {
        "class_options": class_options(include_all=False),
        "class_options_with_all": class_options(include_all=True),
        "class_display": display_class,
        "CANONICAL_CLASSES": CANONICAL_CLASSES,
        "CLASS_DISPLAY_LABELS": DISPLAY_LABELS,
    }


@app.context_processor
def inject_jee():
    """JEE Main session countdown data for every page (header chips + dashboard cards)."""
    try:
        return {"jee_sessions": jee_sessions_state()}
    except Exception:
        return {"jee_sessions": []}


@app.context_processor
def inject_notifications():
    """Provide unread notification count for student nav badge."""
    data = {"unread_notif_count": 0, "student_notifications": []}
    sid = session.get("student_id")
    if not sid:
        return data
    try:
        with get_db() as conn:
            student = conn.execute(
                "SELECT class_name FROM students WHERE id=?", (sid,)
            ).fetchone()
            class_name = student["class_name"] if student else ""
            rows = conn.execute(
                """SELECT n.* FROM notifications n
                   WHERE (n.class_name='' OR n.class_name IS NULL OR n.class_name=?)
                   AND n.id NOT IN (
                     SELECT notification_id FROM notification_reads WHERE student_id=?
                   )
                   ORDER BY n.created_at DESC LIMIT 20""",
                (class_name, sid)
            ).fetchall()
            data["student_notifications"] = rows
            data["unread_notif_count"] = len(rows)
    except Exception:
        pass
    return data
