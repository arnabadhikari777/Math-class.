# -*- coding: utf-8 -*-
"""Template context processors (class options, JEE widget, notifications)."""

from flask import session

from backend.config import PUSH_ENABLED
from backend.core import app
from backend.db.connection import get_db


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


@app.template_filter("marks")
def format_marks(value):
    """21.0 -> '21', 2.5 -> '2.5', -1.0 -> '-1', None -> '0' (marks are stored as numbers, shown without '.0')."""
    try:
        v = round(float(value), 4)
    except (TypeError, ValueError):
        return "0"
    return str(int(v)) if v == int(v) else ("%.4f" % v).rstrip("0").rstrip(".")


@app.context_processor
def inject_push_flag():
    """Templates hide the "Enable alerts" button when no VAPID keys are configured."""
    return {"push_enabled": PUSH_ENABLED}
