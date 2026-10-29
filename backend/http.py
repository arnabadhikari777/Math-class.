# -*- coding: utf-8 -*-
"""Shared HTTP helpers: JSON envelope, auth decorators for /api/v1, pagination.

Envelope
    success: {"ok": true,  "data": ..., "meta": {...optional}}
    failure: {"ok": false, "error": {"code": "...", "message": "..."}}

Auth uses the session keys app.py already sets (student_id, is_teacher,
impersonating), so no second login system exists.
"""
from functools import wraps

from flask import jsonify, request, session


def ok(data=None, meta=None, status=200):
    body = {"ok": True, "data": data}
    if meta is not None:
        body["meta"] = meta
    return jsonify(body), status


def fail(code, message, status=400):
    return jsonify({"ok": False, "error": {"code": code, "message": message}}), status


def student_api(f):
    """Logged-in student (a teacher 'viewing as' a student counts as that student)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("student_id"):
            return fail("unauthenticated", "Please log in.", 401)
        return f(*args, **kwargs)
    return wrapper


def teacher_api(f):
    """Teacher only. A teacher who is impersonating a student is treated as the
    student, so teacher-level API access is refused until they return."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_teacher"):
            code, status = ("forbidden", 403) if session.get("student_id") else ("unauthenticated", 401)
            return fail(code, "Teacher access required.", status)
        if session.get("impersonating"):
            return fail("forbidden", "Return to the teacher session first.", 403)
        return f(*args, **kwargs)
    return wrapper


def paging(default_per_page=20, max_per_page=100):
    """Parse ?page=&per_page= safely. Returns (page, per_page, offset)."""
    def _int(name, default):
        try:
            return int(request.args.get(name, default))
        except (TypeError, ValueError):
            return default
    page = max(1, _int("page", 1))
    per_page = min(max_per_page, max(1, _int("per_page", default_per_page)))
    return page, per_page, (page - 1) * per_page


def page_meta(page, per_page, total):
    return {"page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page if per_page else 0}


def row_dict(row, drop=()):
    return {k: row[k] for k in row.keys() if k not in drop}
