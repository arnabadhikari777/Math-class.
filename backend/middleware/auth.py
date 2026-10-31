# -*- coding: utf-8 -*-
"""Session validation and student / teacher login-required decorators."""

from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

from backend.core import app
from backend.db.connection import get_db


@app.before_request
def validate_student_session():
    """Ends a student session when the password was reset (session_version
    changed) or the account got blocked/deleted — this is what makes
    'logout from all devices' work with cookie-based sessions."""
    sid = session.get("student_id")
    if not sid or request.endpoint in (None, "static", "manifest", "service_worker"):
        return None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT session_version, is_blocked FROM students WHERE id=?", (sid,)
            ).fetchone()
    except Exception:
        return None
    if row is not None and not row["is_blocked"] and (row["session_version"] or 0) == session.get("sv", 0):
        return None
    for k in ("student_id", "student_name", "student_class", "sv", "impersonating"):
        session.pop(k, None)
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Session ended. Please log in again."}), 401
    flash("Your session has ended. Please log in again.", "error")
    return redirect(url_for("login"))


def student_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("student_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def teacher_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_teacher"):
            flash("Teacher login is required.", "error")
            return redirect(url_for("teacher_login"))
        return f(*args, **kwargs)
    return decorated
