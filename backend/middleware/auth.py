# -*- coding: utf-8 -*-
"""Session validation and student / teacher login-required decorators."""

import secrets
from datetime import datetime, timedelta
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


def start_session(remember=True):
    """Brand-new session at login (no fixation: nothing of the old one survives).
    remember=True  -> persistent cookie (PERMANENT_SESSION_LIFETIME, 30 days): survives closing the browser.
    remember=False -> browser-session cookie: gone when the browser closes.
    The cookie is HttpOnly + SameSite=Lax (+ Secure on HTTPS) and holds only ids - never the password."""
    session.clear()
    session.permanent = bool(remember)
    session["jti"] = secrets.token_urlsafe(16)


def revoke_current_session():
    """Logout: remember this session id as dead, so a copied cookie stops working at once."""
    jti = session.get("jti")
    if not jti:
        return
    now = datetime.now()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM revoked_sessions WHERE expires_at < ?", (now.isoformat(),))
            conn.execute("INSERT OR IGNORE INTO revoked_sessions (jti, revoked_at, expires_at) VALUES (?,?,?)",
                         (jti, now.isoformat(), (now + app.config["PERMANENT_SESSION_LIFETIME"]).isoformat()))
            conn.commit()
    except Exception:
        app.logger.exception("could not record the logout (revoked_sessions)")


_SKIP_ENDPOINTS = (None, "static", "manifest", "service_worker")
_STUDENT_KEYS = ("student_id", "student_name", "student_class", "sv", "impersonating",
                 "impersonation_log_id", "impersonation_started")


def _session_error(message, status):
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"ok": False, "error": message}), status
    return None


@app.before_request
def validate_session():
    """ONE database round-trip per request (it used to be two) that checks both:

    * a logged-out session id (jti) can never be used again, also for API calls;
    * a student session ends when the password was reset (session_version changed) or the
      account was blocked/archived/deleted - this is what makes 'logout from all devices' work.

    Requests with no login cookie cost nothing. If the check itself cannot run (database error)
    the request is refused with 503 and logged; it is never silently allowed.
    """
    jti = session.get("jti")
    sid = session.get("student_id")
    if not (jti or sid) or request.endpoint in _SKIP_ENDPOINTS:
        return None
    try:
        with get_db() as conn:
            dead = conn.execute("SELECT 1 FROM revoked_sessions WHERE jti=?", (jti,)).fetchone() if jti else None
            row = conn.execute(
                "SELECT session_version, is_blocked, is_archived FROM students WHERE id=?", (sid,)
            ).fetchone() if (sid and not dead) else None
    except Exception:
        app.logger.exception("session validation failed (database)")
        err = _session_error("Service temporarily unavailable. Please try again.", 503)
        if err:
            return err
        return ("Service temporarily unavailable. Please try again in a moment.", 503)
    if dead:
        session.clear()
        err = _session_error("Session ended. Please log in again.", 401)
        return err or redirect(url_for("login"))
    if sid and not (row is not None and not row["is_blocked"] and not row["is_archived"]
                    and (row["session_version"] or 0) == session.get("sv", 0)):
        for k in _STUDENT_KEYS:
            session.pop(k, None)
        err = _session_error("Session ended. Please log in again.", 401)
        if err:
            return err
        flash("Your session has ended. Please log in again.", "error")
        return redirect(url_for("login"))
    return None


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
