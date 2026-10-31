# -*- coding: utf-8 -*-
"""Read-side business logic for a logged-in student (profile, tests, results,
notes, announcements, progress). Pure functions: take a connection, return dicts.

Scoring rules are NOT re-implemented here - they are read back exactly as
the web pages stored them (results.score / results.total_marks, PASS_PERCENTAGE).
"""
from flask import current_app

from backend.db import escape_like

VISIBLE = "(class_name=? OR class_name='' OR class_name IS NULL)"
PROFILE_FIELDS = ("id", "name", "class_name", "roll", "email", "mobile", "registered_at")


def pass_percentage():
    return current_app.config.get("PASS_PERCENTAGE", 40)


def percentage(score, total):  # identical to app.percentage()
    if not total or total <= 0:
        return 0
    return round((score / total) * 100, 1)


def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def get_student(conn, sid):
    return conn.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()


def profile(conn, sid):
    row = get_student(conn, sid)
    return {k: row[k] for k in PROFILE_FIELDS} if row else None


def list_tests(conn, student, status="all", page=1, per_page=20, offset=0):
    base = f"""
        FROM tests t WHERE t.is_active=1
          AND (t.class_name=? OR t.class_name='' OR t.class_name IS NULL)"""
    inner = f"""SELECT t.id, t.title, t.subject, t.class_name, t.duration_minutes, t.created_at,
            (SELECT COUNT(*) FROM questions q WHERE q.test_id=t.id) AS question_count,
            (SELECT COALESCE(SUM(q.marks),0) FROM questions q WHERE q.test_id=t.id) AS total_marks,
            (SELECT r.id FROM results r WHERE r.test_id=t.id AND r.student_id=?) AS result_id
            {base}"""
    params = [student["id"], student["class_name"]]
    where = ""
    if status == "available":
        where = " WHERE result_id IS NULL AND question_count > 0"
    elif status == "attempted":
        where = " WHERE result_id IS NOT NULL"
    total = conn.execute(f"SELECT COUNT(*) FROM ({inner}){where}", params).fetchone()[0]
    items = _rows(conn.execute(
        f"SELECT * FROM ({inner}){where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]))
    for it in items:
        it["attempted"] = it["result_id"] is not None
    return items, total


def list_results(conn, sid, per_page, offset):
    from backend.services.exam_service import RELEASED_SQL
    total = conn.execute(
        "SELECT COUNT(*) FROM results r JOIN tests t ON t.id=r.test_id WHERE r.student_id=? AND " + RELEASED_SQL,
        (sid,)).fetchone()[0]
    items = _rows(conn.execute(
        """SELECT r.id, r.test_id, t.title AS test_title, r.score, r.total_marks, r.submitted_at
           FROM results r JOIN tests t ON t.id=r.test_id
           WHERE r.student_id=? AND """ + RELEASED_SQL + """ ORDER BY r.submitted_at DESC LIMIT ? OFFSET ?""",
        (sid, per_page, offset)))
    for it in items:
        it["percentage"] = percentage(it["score"], it["total_marks"])
        it["passed"] = it["percentage"] >= pass_percentage()
    return items, total


def result_detail(conn, sid, test_id):
    r = conn.execute(
        """SELECT r.*, t.title AS test_title FROM results r JOIN tests t ON t.id=r.test_id
           WHERE r.test_id=? AND r.student_id=?""", (test_id, sid)).fetchone()
    if r is None:
        return None
    from backend.services.exam_service import results_visible
    t = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
    if t is not None and not results_visible(t):      # submitted, marks not released yet: no score, no answers
        return {"result_id": r["id"], "test_id": r["test_id"], "test_title": r["test_title"],
                "submitted_at": r["submitted_at"], "released": False}
    answers = _rows(conn.execute(
        """SELECT a.question_id, a.selected_option, a.is_correct, a.question_text,
                  a.option_a, a.option_b, a.option_c, a.option_d, a.correct_option, a.marks, a.marks_awarded
           FROM answers a WHERE a.result_id=? ORDER BY a.position, a.id""", (r["id"],)))
    pct = percentage(r["score"], r["total_marks"])
    return {"result_id": r["id"], "test_id": r["test_id"], "test_title": r["test_title"], "released": True,
            "score": r["score"], "total_marks": r["total_marks"], "submitted_at": r["submitted_at"],
            "correct": r["correct_count"], "wrong": r["wrong_count"], "unanswered": r["unanswered_count"],
            "positive_marks": r["positive_marks"], "negative_marks": r["negative_marks_total"],
            "percentage": pct, "passed": pct >= pass_percentage(), "answers": answers}


def list_notes(conn, student, q, per_page, offset):
    where, params = f"WHERE {VISIBLE}", [student["class_name"]]
    if q:
        where += " AND (title LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\')"
        like = f"%{escape_like(q)}%"
        params += [like, like]
    total = conn.execute(f"SELECT COUNT(*) FROM notes {where}", params).fetchone()[0]
    items = _rows(conn.execute(
        f"""SELECT id, title, subject, class_name, uploaded_at FROM notes {where}
            ORDER BY uploaded_at DESC LIMIT ? OFFSET ?""", params + [per_page, offset]))
    for it in items:
        it["download_url"] = f"/notes/download/{it['id']}"
    return items, total


def list_announcements(conn, student, per_page, offset):
    where, params = f"WHERE is_active=1 AND {VISIBLE}", [student["class_name"]]
    total = conn.execute(f"SELECT COUNT(*) FROM announcements {where}", params).fetchone()[0]
    items = _rows(conn.execute(
        f"""SELECT id, title, content, class_name, created_at FROM announcements {where}
            ORDER BY created_at DESC LIMIT ? OFFSET ?""", params + [per_page, offset]))
    return items, total


def unread_notifications(conn, student):
    return conn.execute(
        """SELECT COUNT(*) FROM notifications n
           WHERE (n.class_name='' OR n.class_name IS NULL OR n.class_name=?)
             AND n.id NOT IN (SELECT notification_id FROM notification_reads WHERE student_id=?)""",
        (student["class_name"], student["id"])).fetchone()[0]


def summary(conn, student):
    sid = student["id"]
    from backend.services.exam_service import RELEASED_SQL
    results = conn.execute(
        "SELECT r.score, r.total_marks FROM results r JOIN tests t ON t.id=r.test_id WHERE r.student_id=? AND " + RELEASED_SQL,
        (sid,)).fetchall()
    attempted = len(results)
    avg = round(sum(percentage(r["score"], r["total_marks"]) for r in results) / attempted, 1) if attempted else 0
    pending = list_tests(conn, student, "available", 1, 1, 0)[1]
    return {"tests_attempted": attempted, "average_percentage": avg,
            "tests_available": pending,
            "unread_notifications": unread_notifications(conn, student)}


def progress(conn, sid):
    """Chronological list of the student's (released) results for trend charts."""
    from backend.services.exam_service import RELEASED_SQL as RELEASED_SQL_
    rows = _rows(conn.execute(
        """SELECT r.test_id, t.title AS test_title, t.subject, r.score, r.total_marks, r.submitted_at
           FROM results r JOIN tests t ON t.id=r.test_id
           WHERE r.student_id=? AND """ + RELEASED_SQL_ + """ ORDER BY r.submitted_at ASC LIMIT 200""", (sid,)))
    for r in rows:
        r["percentage"] = percentage(r["score"], r["total_marks"])
    return rows
