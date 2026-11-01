# -*- coding: utf-8 -*-
"""Teacher dashboard and activity log."""

from flask import render_template

from backend.core import app
from backend.db.connection import get_db
from backend.middleware.auth import teacher_login_required


@app.route("/teacher/dashboard")
@teacher_login_required
def teacher_dashboard():
    with get_db() as conn:
        total_students = conn.execute("SELECT COUNT(*) AS c FROM students WHERE is_verified=1").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) AS c FROM students WHERE is_verified=0").fetchone()["c"]
        total_notes = conn.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
        total_tests = conn.execute("SELECT COUNT(*) AS c FROM tests").fetchone()["c"]
        total_results = conn.execute("SELECT COUNT(*) AS c FROM results").fetchone()["c"]
        new_contacts = conn.execute("SELECT COUNT(*) AS c FROM contact_requests WHERE status='new'").fetchone()["c"]
        recent_logins = conn.execute(
            """SELECT lh.login_time, lh.ip_address, lh.via_teacher, s.name, s.class_name
               FROM login_history lh JOIN students s ON s.id = lh.student_id
               ORDER BY lh.login_time DESC LIMIT 10"""
        ).fetchall()
        announcements = conn.execute("SELECT * FROM announcements ORDER BY created_at DESC LIMIT 5").fetchall()
        recent_tests = conn.execute(
            """SELECT t.id, t.title, t.subject, t.class_name, t.status, t.created_at,
                      (SELECT COUNT(*) FROM questions q WHERE q.test_id = t.id) AS q_count,
                      (SELECT COUNT(*) FROM results r WHERE r.test_id = t.id) AS r_count
               FROM tests t WHERE t.is_archived = 0 ORDER BY t.created_at DESC LIMIT 5"""
        ).fetchall()
        recent_results = conn.execute(
            """SELECT r.id, r.test_id, r.score, r.total_marks, r.submitted_at, s.name AS student_name, t.title AS test_title
               FROM results r JOIN students s ON s.id = r.student_id JOIN tests t ON t.id = r.test_id
               ORDER BY r.submitted_at DESC LIMIT 6"""
        ).fetchall()
    return render_template(
        "teacher/teacher_dashboard.html", recent_tests=recent_tests, recent_results=recent_results,
        total_students=total_students, pending=pending,
        total_notes=total_notes, total_tests=total_tests,
        total_results=total_results, new_contacts=new_contacts,
        recent_logins=recent_logins, announcements=announcements
    )


@app.route("/teacher/activity")
@teacher_login_required
def teacher_activity():
    with get_db() as conn:
        logs = conn.execute("SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 100").fetchall()
    return render_template("teacher/teacher_activity.html", logs=logs)
