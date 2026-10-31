# -*- coding: utf-8 -*-
"""Tiny unauthenticated endpoints useful for the PWA / monitoring."""
from flask import Blueprint, session

from backend.db import get_db
from backend.utils.api_response import ok

bp = Blueprint("api_public", __name__, url_prefix="/api/v1")


@bp.get("/health")
def health():
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    return ok({"status": "ok" if db_ok else "degraded", "database": db_ok})


@bp.get("/csrf")
def csrf():
    """CSRF token for JS clients (send it back as the X-CSRFToken header on POST)."""
    from flask import current_app
    token = current_app.jinja_env.globals["csrf_token"]()
    return ok({"csrf_token": token, "header": "X-CSRFToken"})


@bp.get("/session")
def whoami():
    """What the PWA needs to decide which screen to show; no personal data."""
    role = "teacher" if session.get("is_teacher") and not session.get("student_id") else (
        "student" if session.get("student_id") else "anonymous")
    return ok({"role": role, "impersonating": bool(session.get("impersonating"))})
