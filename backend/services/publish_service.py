# -*- coding: utf-8 -*-
"""Draft -> Published workflow for tests.

Invariant kept for the whole site: a student can only see a test when ``is_active = 1``, and
``is_active`` is 1 only for a published test. So every existing student query keeps working.

Notifications are sent ONLY by :func:`claim_notification`, which flips ``notified_at`` with a
conditional UPDATE - two clicks (or two tabs) can never both win, so no duplicates.
"""
from datetime import datetime

OPTIONS = ("A", "B", "C", "D")


def _now():
    return datetime.now().isoformat()


def total_marks(conn, test_id):
    return conn.execute("SELECT COALESCE(SUM(marks),0) FROM questions WHERE test_id=?", (test_id,)).fetchone()[0]


def problems(conn, test):
    """List of human-readable reasons why this test cannot be published (empty list = OK)."""
    out = []
    if not (test["title"] or "").strip():
        out.append("The test needs a title.")
    if not (test["class_name"] or "").strip():
        out.append("The test needs a class.")
    try:
        if int(test["duration_minutes"] or 0) < 1:
            out.append("Duration must be at least 1 minute.")
    except (TypeError, ValueError):
        out.append("Duration is not a valid number.")
    if float(test["negative_marks"] or 0) < 0:
        out.append("Negative marks cannot be below 0.")
    qs = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test["id"],)).fetchall()
    if not qs:
        out.append("Add at least one question before publishing.")
    for n, q in enumerate(qs, 1):
        if not (q["question_text"] or "").strip() and not q["image_url"]:
            out.append(f"Question {n}: needs text or an image.")
        if not all((q[k] or "").strip() for k in ("option_a", "option_b", "option_c", "option_d")):
            out.append(f"Question {n}: all four options must be filled.")
        if (q["correct_option"] or "").strip().upper() not in OPTIONS:
            out.append(f"Question {n}: choose the correct answer (A-D).")
        if int(q["marks"] or 0) < 1:
            out.append(f"Question {n}: marks must be at least 1.")
    return out


def publish(conn, test_id):
    """-> (state, detail)  state in 'published', 'already', 'invalid', 'missing'.
    Caller commits, then calls claim_notification() and sends the notification only if it returns True."""
    test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
    if test is None:
        return "missing", []
    bad = problems(conn, test)
    if bad:
        return "invalid", bad
    cur = conn.execute(
        """UPDATE tests SET status='published', is_active=1, published_at=COALESCE(published_at, ?)
           WHERE id=? AND (status!='published' OR is_active=0) AND is_archived=0""", (_now(), test_id))
    return ("published" if cur.rowcount == 1 else "already"), []


def unpublish(conn, test_id):
    """Back to draft: hidden from students at once. Running attempts keep their own snapshot/deadline."""
    cur = conn.execute("UPDATE tests SET status='draft', is_active=0 WHERE id=? AND status='published'", (test_id,))
    return cur.rowcount == 1


def claim_notification(conn, test_id):
    """True for exactly ONE caller per test (first publish). Re-publish returns False."""
    cur = conn.execute("UPDATE tests SET notified_at=? WHERE id=? AND notified_at IS NULL", (_now(), test_id))
    return cur.rowcount == 1


def force_notification_stamp(conn, test_id):
    """Teacher explicitly pressed 'Notify students again'."""
    conn.execute("UPDATE tests SET notified_at=? WHERE id=?", (_now(), test_id))


def is_editable(test):
    """Questions and rules may only change while the test is a draft."""
    return (test["status"] or "draft") == "draft"
