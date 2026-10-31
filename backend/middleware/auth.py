# -*- coding: utf-8 -*-
"""Session validation and student / teacher login-required decorators."""

from functools import wraps

from flask import flash, jsonify, redirect, request, session, url_for

from backend.core import app
from backend.db.connection import get_db


# The only writes a teacher may make while viewing as a student: leaving that mode.
IMPERSONATION_WRITE_OK = {"logout", "static"}


@app.before_request
def impersonation_guard():
    """Teacher viewing as a student is READ-ONLY, and the view expires after an hour."""
    if not session.get("impersonating"):
        return None
    from backend.services import impersonation_service
    if impersonation_service.expired(session.get("impersonation_started")):
        from backend.routes.web.auth import end_impersonation_response
        impersonation_service.end(session.get("impersonation_log_id"), "timeout")
        flash("The view-as-student session expired.", "error")
        return end_impersonation_response()
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.endpoint not in IMPERSONATION_WRITE_OK:
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"ok": False, "error": "Read-only while viewing as a student."}), 403
        flash("Read-only: a teacher viewing as a student cannot change anything. Use Exit to Teacher.", "error")
        return redirect(url_for("student_dashboard"))
    return None


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
                "SELECT session_version, is_blocked, is_archived FROM students WHERE id=?", (sid,)
            ).fetchone()
    except Exception:
        return None
    if row is not None and not row["is_blocked"] and not row["is_archived"] and (row["session_version"] or 0) == session.get("sv", 0):
        return None
    for k in ("student_id", "student_name", "student_class", "sv", "impersonating", "impersonation_log_id",
              "impersonation_started"):
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
        if session.get("impersonating") or not session.get("is_teacher"):
            flash("Teacher login is required.", "error")
            return redirect(url_for("teacher_login"))
        return f(*args, **kwargs)
    return decorated
