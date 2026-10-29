# -*- coding: utf-8 -*-
"""Cross-cutting protections that wrap EXISTING routes without editing them.

They are registered once from ``register_backend`` and key off Flask's
``request.endpoint``, so the original view functions in app.py stay as-is.

1. Class access: a student can only open / submit / see the leaderboard of a
   test, or download a note, that is visible on their own dashboard
   (same rule app.py uses: class matches, or the item has no class).
2. Test attempts: remember when a student opened a test. Optional server-side
   time-limit check (off by default: ENFORCE_TEST_TIMER=1 to turn on).
3. Private-data caching: every non-static response is ``Cache-Control: no-store``
   so browsers / proxies / the service worker never keep per-user pages.
4. A duplicate result insert (double click / two tabs) is turned into a
   friendly "already submitted" instead of a 500.
"""
import os
import sqlite3
from datetime import datetime

from flask import abort, flash, jsonify, redirect, request, session, url_for

from .db import get_db

TEST_ENDPOINTS = {"take_test", "submit_test", "test_leaderboard"}
TIMER_GRACE_SECONDS = 120


def _enforce_timer():
    return os.environ.get("ENFORCE_TEST_TIMER", "0").strip().lower() in ("1", "true", "yes")


def _visible(item_class, student_class):
    return not item_class or item_class == student_class


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
        if ep != "submit_test":
            return None
        try:
            attempt = conn.execute(
                "SELECT started_at FROM test_attempts WHERE test_id=? AND student_id=?",
                (test["id"], sid)).fetchone()
        except sqlite3.Error:
            attempt = None
    # A test switched off by the teacher cannot be started or submitted from scratch,
    # but a student who already opened it may still hand in what they wrote.
    if not test["is_active"] and attempt is None:
        abort(404)
    if attempt and _enforce_timer():
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(attempt["started_at"])).total_seconds()
        except ValueError:
            elapsed = 0
        if elapsed > (test["duration_minutes"] or 0) * 60 + TIMER_GRACE_SECONDS:
            flash("Time limit exceeded - this submission was not accepted.", "error")
            return redirect(url_for("student_dashboard"))
    return None


def after_guard(resp):
    ep = request.endpoint
    # remember the first time a student opened a test
    if ep == "take_test" and resp.status_code == 200 and session.get("student_id"):
        try:
            tid = (request.view_args or {}).get("test_id")
            with get_db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO test_attempts (test_id, student_id, started_at) VALUES (?,?,?)",
                    (tid, session["student_id"], datetime.now().isoformat()))
                conn.commit()
        except Exception:
            pass  # never break the page because of bookkeeping
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
