# -*- coding: utf-8 -*-
"""Cross-cutting protections that wrap EXISTING routes without editing them.

They are registered once from ``register_backend`` and key off Flask's
``request.endpoint``, so the view functions in backend/routes/ stay untouched.

1. Class access: a student can only open / submit / see the leaderboard of a
   test, or download a note, that is visible on their own dashboard
   (same rule the dashboard uses: class matches, or the item has no class).
2. Test attempts / timer: moved to backend/services/exam_service.py (server-side, always on;
   ENFORCE_TEST_TIMER=0 is an emergency kill-switch only).
3. Private-data caching: every non-static response is ``Cache-Control: no-store``
   so browsers / proxies / the service worker never keep per-user pages.
4. A duplicate result insert (double click / two tabs) is turned into a
   friendly "already submitted" instead of a 500.
"""
import sqlite3

from flask import abort, flash, jsonify, redirect, request, session, url_for

from backend.db import get_db

TEST_ENDPOINTS = {"take_test", "test_continue", "test_confirm", "test_start", "test_exam",
                  "submit_test", "test_leaderboard", "save_test_progress"}


def _visible(item_class, student_class):
    """True if content with item_class is visible to a student in student_class.

    Empty / NULL item_class means available to all classes. Comparison uses
    canonical class values so legacy labels (Six, Nine, …) still match.
    """
    if not item_class or not str(item_class).strip():
        return True
    try:
        from backend.utils.classes import normalize_class
        ni = normalize_class(item_class, allow_empty=True) or str(item_class).strip()
        ns = normalize_class(student_class, allow_empty=True) or str(student_class or "").strip()
        return ni == ns
    except Exception:
        return item_class == student_class


def access_guard():
    ep = request.endpoint
    sid = session.get("student_id")
    if not sid or (ep not in TEST_ENDPOINTS and ep != "download_note"):
        return None  # anonymous users are handled by the route's own login decorator
    args = request.view_args or {}
    with get_db() as conn:
        student = conn.execute("SELECT class_name FROM students WHERE id=?", (sid,)).fetchone()
        if student is None:
            return None
        if ep == "download_note":
            note = conn.execute("SELECT class_name FROM notes WHERE id=?", (args.get("note_id"),)).fetchone()
            if note is not None and not _visible(note["class_name"], student["class_name"]):
                abort(404)
            return None
        test = conn.execute("SELECT * FROM tests WHERE id=?", (args.get("test_id"),)).fetchone()
        if test is None:
            return None  # the view itself returns its normal 404
        if not _visible(test["class_name"], student["class_name"]):
            abort(404)
        if test["is_archived"] and ep != "submit_test":
            abort(404)
        # Timer, attempt limits and "already submitted" are decided by services/exam_service.py
        # from the database (server clock), not here.
    return None


def after_guard(resp):
    ep = request.endpoint
    # no per-user page may be stored by browser/proxy/service worker
    path = request.path
    if ep != "static" and not path.startswith("/static/"):
        if path == "/sw.js":
            resp.headers["Cache-Control"] = "no-cache"
        elif path == "/manifest.json":
            resp.headers.setdefault("Cache-Control", "public, max-age=3600")
        else:
            resp.headers["Cache-Control"] = "no-store"
            resp.headers.add("Vary", "Cookie")
    return resp


def integrity_error(exc):
    """UNIQUE violation, e.g. two tabs submitting the same test at once."""
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": {"code": "conflict",
                                                "message": "Duplicate or conflicting request."}}), 409
    if request.endpoint == "submit_test":
        flash("Already submitted.", "error")
        return redirect(url_for("student_dashboard"))
    raise exc


def register(app):
    app.before_request(access_guard)
    app.after_request(after_guard)
    app.register_error_handler(sqlite3.IntegrityError, integrity_error)
